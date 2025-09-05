from __future__ import annotations

import importlib
import os
from functools import partial
from typing import Any, Literal, Optional

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from ..const import (
    LOGGER,
    AllowedPlugins,
    XTDeviceEntityFunctions,
    XTMultiManagerProperties,
)
from ..entity_parser.entity_parser import XTCustomEntityParser
from ..util import append_lists
from .refactor.device_merger import DeviceMerger
from .refactor.message_handler import MessageHandler
from .shared.cloud_fix import CloudFixes
from .shared.debug.debug_helper import DebugHelper
from .shared.interface.device_manager import XTDeviceManagerInterface
from .shared.multi_device_listener import MultiDeviceListener
from .shared.multi_mq import MultiMQTTQueue
from .shared.multi_source_handler import MultiSourceHandler
from .shared.multi_virtual_function_handler import XTVirtualFunctionHandler
from .shared.multi_virtual_state_handler import XTVirtualStateHandler
from .shared.shared_classes import (
    DeviceWatcher,
    XTConfigEntry,  # noqa: F811
    XTDevice,
    XTDeviceMap,
)
from .shared.threading import XTThreadingManager


class MultiManager:  # noqa: F811
    """A manager for all Tuya managers."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.virtual_state_handler = XTVirtualStateHandler(self)
        self.virtual_function_handler = XTVirtualFunctionHandler(self)
        self.multi_mqtt_queue: MultiMQTTQueue = MultiMQTTQueue(self)
        self.multi_device_listener: MultiDeviceListener = MultiDeviceListener(hass, self)
        self.hass = hass
        self.multi_source_handler = MultiSourceHandler(self)
        self.device_watcher = DeviceWatcher(self)
        self.accounts: dict[str, XTDeviceManagerInterface] = {}
        self.master_device_map: XTDeviceMap = XTDeviceMap({})
        self.is_ready_for_messages = False
        self.pending_messages: list[tuple[str, dict]] = []
        self.devices_shared: dict[str, XTDevice] = {}
        self.debug_helper = DebugHelper(self)
        self.scene_id: list[str] = []
        self.general_properties: dict[str, Any] = {}
        self.entity_parsers: dict[str, XTCustomEntityParser] = {}
        self.post_setup_callbacks: list = []
        self.device_merger = DeviceMerger(self)
        self.message_handler = MessageHandler(self)

    @property
    def device_map(self):
        return self.master_device_map

    @property
    def mq(self):
        return self.multi_mqtt_queue

    def get_account_by_name(
        self, account_name: str | None
    ) -> XTDeviceManagerInterface | None:
        if account_name in self.accounts:
            return self.accounts[account_name]
        return None

    async def setup_entry(
        self, hass: HomeAssistant, config_entry: XTConfigEntry
    ) -> None:
        # Load all the plugins
        # subdirs = await self.hass.async_add_executor_job(os.listdir, os.path.dirname(__file__))
        subdirs = AllowedPlugins.get_plugins_to_load()
        for directory in subdirs:
            if os.path.isdir(os.path.dirname(__file__) + os.sep + directory):
                load_path = f".{directory}.init"
                try:
                    plugin = await self.hass.async_add_executor_job(
                        partial(
                            importlib.import_module, name=load_path, package=__package__
                        )
                    )
                    LOGGER.debug(f"Plugin {load_path} loaded")
                    instance: XTDeviceManagerInterface = plugin.get_plugin_instance()
                    if await instance.setup_from_entry(hass, config_entry, self):
                        self.accounts[instance.get_type_name()] = instance
                except ModuleNotFoundError as e:
                    LOGGER.error(f"Loading module failed: {e}")

        for account in self.accounts.values():
            await self.hass.async_add_executor_job(account.on_post_setup)

    async def setup_entity_parsers(self, hass: HomeAssistant) -> None:
        await XTCustomEntityParser.setup_entity_parsers(hass, self)

    def get_domain_identifiers_of_device(self, device_id: str) -> list:
        return_list: list = []
        for account in self.accounts.values():
            return_list = append_lists(
                return_list, account.get_domain_identifiers_of_device(device_id)
            )
        return return_list

    def get_platform_descriptors_to_merge(self, platform: Platform) -> list:
        """Get all platform descriptors to be merged."""
        account_descriptors = [
            desc
            for account in self.accounts.values()
            if (desc := account.get_platform_descriptors_to_merge(platform))
        ]
        parser_descriptors = [
            desc
            for parser in self.entity_parsers.values()
            if (desc := parser.get_descriptors_to_merge(platform))
        ]
        return account_descriptors + parser_descriptors

    def get_platform_descriptors_to_exclude(self, platform: Platform) -> list:
        """Get all platform descriptors to be excluded."""
        return [
            desc
            for account in self.accounts.values()
            if (desc := account.get_platform_descriptors_to_exclude(platform))
        ]

    def update_device_cache(self):
        self.is_ready_for_messages = False
        XTDeviceMap.clear_master_device_map()
        thread_manager: XTThreadingManager = XTThreadingManager()

        def update_device_cache_thread(manager: XTDeviceManagerInterface) -> None:
            manager.update_device_cache()

            # New devices have been created in their own device maps
            # let's convert them to XTDevice
            for device_map in manager.get_available_device_maps():
                for device_id in device_map:
                    device_map[device_id] = manager.convert_to_xt_device(
                        device_map[device_id], device_map.device_source_priority
                    )

        for manager in self.accounts.values():
            thread_manager.add_thread(update_device_cache_thread, manager=manager)

        thread_manager.start_and_wait()

        # Register all devices in the master device map
        self._update_master_device_map()

        # Now let's aggregate all of these devices into a single
        # "All functionnality" device
        self.device_merger.merge_devices()
        for device in self.device_map.values():
            # Applied twice because some parts at the end of apply_fix would change values of previous calls
            CloudFixes.apply_fixes(device)
            CloudFixes.apply_fixes(device)
        self._enable_multi_map_device_alignment()
        self._process_pending_messages()

    def _process_pending_messages(self):
        self.is_ready_for_messages = True
        for messages in self.pending_messages:
            self.on_message(messages[0], messages[1])
        self.pending_messages.clear()

    def _update_master_device_map(self):
        for manager in self.accounts.values():
            for device_map in manager.get_available_device_maps():
                for device_id in device_map:
                    if device_id not in self.master_device_map:
                        self.master_device_map[device_id] = device_map[device_id]

    def get_available_device_maps(self) -> list[XTDeviceMap]:
        return_list: list[XTDeviceMap] = []
        for manager in self.accounts.values():
            for device_map in manager.get_available_device_maps():
                return_list.append(device_map)
        return return_list

    def _enable_multi_map_device_alignment(self):
        for device_map in self.get_available_device_maps():
            XTDeviceMap.register_device_map(device_map)
        XTDeviceMap.register_device_map(self.device_map)
        self._align_multi_map_devices()

    def _align_multi_map_devices(self):
        # Refresh all master device variables with themselves to trigger alignment
        for device in self.device_map.values():
            for key, value in vars(device).items():
                setattr(device, key, value)

    def unload(self):
        for manager in self.accounts.values():
            manager.unload()

    def refresh_mq(self):
        for manager in self.accounts.values():
            manager.refresh_mq()

    def register_device_descriptors(self, name: str, descriptors):
        self.virtual_state_handler.register_device_descriptors(name, descriptors)
        self.virtual_function_handler.register_device_descriptors(name, descriptors)

    def remove_device_listeners(self) -> None:
        for manager in self.accounts.values():
            manager.remove_device_listeners()

    def _read_dpId_from_code(self, code: str, device: XTDevice) -> int | None:
        if not hasattr(device, "local_strategy"):
            return None
        if (
            code in device.status_range
            and hasattr(device.status_range[code], "dp_id")
            and device.status_range[code].dp_id != 0
        ):
            return device.status_range[code].dp_id
        # Ensure caches are in sync
        if (
            getattr(device, "_local_strategy_cache_version", 0)
            != getattr(device, "_local_strategy_version", 0)
        ):
            device._refresh_local_strategy_cache()
        return device.code_to_dpid.get(code)

    def _read_code_from_dpId(self, dpId: int, device: XTDevice) -> str | None:
        if (
            getattr(device, "_local_strategy_cache_version", 0)
            != getattr(device, "_local_strategy_version", 0)
        ):
            device._refresh_local_strategy_cache()
        return device.dpid_to_code.get(dpId)

    def on_message(self, source: str, msg: dict):
        self.message_handler.on_message(source, msg)

    def query_scenes(self) -> list:
        return_list = []
        for account in self.accounts.values():
            return_list = append_lists(return_list, account.query_scenes())
        return return_list

    def send_commands(self, device_id: str, commands: list[dict[str, Any]]):
        virtual_function_commands: list[dict[str, Any]] = []
        regular_commands: list[dict[str, Any]] = []
        if device := self.device_map.get(device_id, None):
            virtual_function_list = (
                self.virtual_function_handler.get_category_virtual_functions(
                    device.category
                )
            )
            for command in commands:
                command_code = command["code"]
                command_value = command["value"]
                LOGGER.debug(f"Base command : {command}")
                vf_found = False
                for virtual_function in virtual_function_list:
                    if (
                        command_code == virtual_function.key
                        or command_code in virtual_function.vf_reset_state
                    ):
                        command_dict = {
                            "code": command_code,
                            "value": command_value,
                            "virtual_function": virtual_function,
                        }
                        virtual_function_commands.append(command_dict)
                        vf_found = True
                        break
                if not vf_found:
                    regular_commands.append(command)

        if virtual_function_commands:
            self.virtual_function_handler.process_virtual_function(
                device_id, virtual_function_commands
            )

        if regular_commands:
            for account in self.accounts.values():
                if account.send_commands(device_id, regular_commands):
                    break

    def get_device_stream_allocate(
        self, device_id: str, stream_type: Literal["flv", "hls", "rtmp", "rtsp"]
    ) -> Optional[str]:
        for account in self.accounts.values():
            if stream_allocate := account.get_device_stream_allocate(
                device_id, stream_type
            ):
                return stream_allocate

    def send_lock_unlock_command(self, device: XTDevice, lock: bool) -> bool:
        for account in self.accounts.values():
            if account.send_lock_unlock_command(device, lock):
                return True
        return False

    def inform_device_has_an_entity(self, device_id: str):
        for account in self.accounts.values():
            account.inform_device_has_an_entity(device_id)

    def trigger_scene(self, home_id: str, scene_id: str):
        for account in self.accounts.values():
            if account.trigger_scene(home_id, scene_id):
                return

    def update_device_online_status(self, device_id: str):
        if device := self.device_map.get(device_id, None):
            old_online_status = device.online
            for online_status in device.online_states:
                device.online = device.online_states[online_status]
                if (
                    device.online
                ):  # Prefer to be more On than Off if multiple state are not in accordance
                    break
            if device.online != old_online_status:
                self.multi_device_listener.update_device(device, None)

    def get_active_types(self) -> list[str]:
        return_list: list[str] = []
        for account in self.accounts.values():
            if account.is_type_initialized():
                return_list.append(account.get_type_name())
        return return_list

    def execute_device_entity_function(
        self,
        function: XTDeviceEntityFunctions,
        device: XTDevice,
        param1: Any | None = None,
        param2: Any | None = None,
    ):
        match function:
            case XTDeviceEntityFunctions.RECALCULATE_PERCENT_SCALE:
                if isinstance(param1, str) and isinstance(param2, int):
                    CloudFixes.fix_incorrect_percent_scale_forced(
                        device, param1, param2
                    )

    async def on_loading_finalized(
        self, hass: HomeAssistant, config_entry: XTConfigEntry
    ):
        for account in self.accounts.values():
            await account.on_loading_finalized(hass, config_entry, self)
        for callback in self.post_setup_callbacks:
            callback()

    def set_general_property(
        self, property_id: XTMultiManagerProperties, property_value: Any
    ):
        self.general_properties[property_id] = property_value

    def get_general_property(
        self, property_id: XTMultiManagerProperties, default: Any | None = None
    ) -> Any | None:
        return self.general_properties.get(property_id, default)
