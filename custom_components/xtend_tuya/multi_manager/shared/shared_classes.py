from __future__ import annotations
from typing import NamedTuple, Any, Optional
from collections import UserDict
from dataclasses import dataclass
import copy
import json
from enum import StrEnum
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from tuya_sharing import (
    CustomerDevice as TuyaDevice,
)
import custom_components.xtend_tuya.multi_manager.multi_manager as mm
import custom_components.xtend_tuya.multi_manager.shared.multi_device_listener as mdl
import custom_components.xtend_tuya.multi_manager.shared.services.services as services
import custom_components.xtend_tuya.util as util
from ...const import (
    LOGGER,
    XTDeviceSourcePriority,
)


class DeviceWatcher:
    def __init__(self, multi_manager: mm.MultiManager) -> None:
        self.watched_dev_id: list[str] = []
        self.multi_manager = multi_manager

    def is_watched(self, dev_id: str) -> bool:
        return dev_id in self.watched_dev_id

    def report_message(self, dev_id: str, message: str, device: XTDevice | None = None):
        if self.is_watched(dev_id):
            if dev_id in self.multi_manager.device_map:
                managed_device = self.multi_manager.device_map[dev_id]
                LOGGER.warning(
                    f"DeviceWatcher for {managed_device.name} ({dev_id}): {message}"
                )
            elif device:
                LOGGER.warning(f"DeviceWatcher for {device.name} ({dev_id}): {message}")
            else:
                LOGGER.warning(f"DeviceWatcher for {dev_id}: {message}")


class HomeAssistantXTData(NamedTuple):
    """Tuya data stored in the Home Assistant data object."""

    multi_manager: mm.MultiManager | None = None
    listener: mdl.MultiDeviceListener | None = None
    service_manager: services.ServiceManager | None = None

    @property
    def manager(self) -> mm.MultiManager | None:
        return self.multi_manager


XTConfigEntry = ConfigEntry[HomeAssistantXTData]


@dataclass
class XTDeviceStatusRange:
    code: str = ""
    type: str | None = None
    values: str = "{}"
    dp_id: int = 0

    def __repr__(self) -> str:
        return f"StatusRange(code={self.code}, type={self.type}, values={self.values}, dp_id={self.dp_id})"

    @staticmethod
    def from_compatible_status_range(status_range: Any):
        if hasattr(status_range, "code"):
            code = status_range.code
        else:
            code = ""
        if hasattr(status_range, "type"):
            type = status_range.type
        else:
            type = ""
        if hasattr(status_range, "values"):
            values = status_range.values
        else:
            values = "{}"
        if hasattr(status_range, "dp_id"):
            dp_id = status_range.dp_id
        else:
            dp_id = 0
        return XTDeviceStatusRange(code=code, type=type, values=values, dp_id=dp_id)


@dataclass
class XTDeviceFunction:
    code: str = ""
    type: str | None = None
    desc: str = ""
    name: str = ""
    values: str = "{}"
    dp_id: int = 0

    def __repr__(self) -> str:
        return f"Function(code={self.code}, type={self.type}, desc={self.desc}, name={self.name}, values={self.values}, dp_id={self.dp_id})"

    @staticmethod
    def from_compatible_function(function: Any):
        if hasattr(function, "code"):
            code = function.code
        else:
            code = ""
        if hasattr(function, "type"):
            type = function.type
        else:
            type = ""
        if hasattr(function, "values"):
            values = function.values
        else:
            values = "{}"
        if hasattr(function, "desc"):
            desc = function.desc
        else:
            desc = ""
        if hasattr(function, "name"):
            name = function.name
        else:
            name = ""
        if hasattr(function, "dp_id"):
            dp_id = function.dp_id
        else:
            dp_id = 0
        return XTDeviceFunction(
            code=code, type=type, desc=desc, name=name, values=values, dp_id=dp_id
        )


class LocalStrategyItemDict(dict):
    """Dictionary for a single local strategy entry that invalidates caches on change."""

    def __init__(self, parent: LocalStrategyDict, *args, **kwargs):  # type: ignore # noqa: F821
        super().__init__(*args, **kwargs)
        self.parent = parent

    def _invalidate(self) -> None:
        # Inform the device that caches are now stale
        self.parent.device._invalidate_local_strategy_cache()

    # Override mutating methods
    def __setitem__(self, key, value) -> None:  # type: ignore[override]
        super().__setitem__(key, value)
        self._invalidate()

    def __delitem__(self, key) -> None:  # type: ignore[override]
        super().__delitem__(key)
        self._invalidate()

    def clear(self) -> None:  # type: ignore[override]
        super().clear()
        self._invalidate()

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        super().update(*args, **kwargs)
        self._invalidate()

    def pop(self, *args, **kwargs):  # type: ignore[override]
        result = super().pop(*args, **kwargs)
        self._invalidate()
        return result

    def popitem(self):  # type: ignore[override]
        result = super().popitem()
        self._invalidate()
        return result


class LocalStrategyDict(dict):
    """Dictionary wrapper that keeps a device cache in sync."""

    def __init__(self, device: XTDevice, *args, **kwargs):  # type: ignore # noqa: F821
        super().__init__()
        self.device = device
        self.update(dict(*args, **kwargs))

    def _wrap_value(self, value):
        if isinstance(value, dict) and not isinstance(value, LocalStrategyItemDict):
            return LocalStrategyItemDict(self, value)
        return value

    def __setitem__(self, key, value) -> None:  # type: ignore[override]
        super().__setitem__(key, self._wrap_value(value))
        self.device._invalidate_local_strategy_cache()

    def __delitem__(self, key) -> None:  # type: ignore[override]
        super().__delitem__(key)
        self.device._invalidate_local_strategy_cache()

    def clear(self) -> None:  # type: ignore[override]
        super().clear()
        self.device._invalidate_local_strategy_cache()

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        for k, v in dict(*args, **kwargs).items():
            super().__setitem__(k, self._wrap_value(v))
        self.device._invalidate_local_strategy_cache()

    def pop(self, *args, **kwargs):  # type: ignore[override]
        result = super().pop(*args, **kwargs)
        self.device._invalidate_local_strategy_cache()
        return result

    def popitem(self):  # type: ignore[override]
        result = super().popitem()
        self.device._invalidate_local_strategy_cache()
        return result


class XTDevice(TuyaDevice):
    id: str
    name: str
    local_key: str
    category: str
    product_id: str
    product_name: str
    sub: bool
    uuid: str
    asset_id: str
    online: bool
    icon: str
    ip: str
    time_zone: str
    active_time: int
    create_time: int
    update_time: int
    set_up: Optional[bool] = False
    support_local: Optional[bool] = False
    # Local strategy mapping, wrapped to invalidate caches on modifications
    local_strategy: dict[int, dict[str, Any]] = {}
    # Cached lookups for quick code/dpId translations
    code_to_dpid: dict[str, int]
    dpid_to_code: dict[int, str]
    source: str
    online_states: dict[str, bool]
    data_model: dict[str, Any]
    function: dict[str, XTDeviceFunction]
    status_range: dict[str, XTDeviceStatusRange]
    force_open_api: Optional[bool] = False
    device_source_priority: int | None = None
    force_compatibility: bool = (
        False  # Force the device functions/status_range/state to remain untouched after merging
    )
    device_preference: dict[str, Any] = {}
    original_device: Any = None
    device_map: XTDeviceMap | None = None

    FIELDS_TO_EXCLUDE_FROM_SYNC: list[str] = [
        "id",
        "device_map",
        "device_source_priority",
        "original_device",
        "source",
        "code_to_dpid",
        "dpid_to_code",
        "_local_strategy",
        "_local_strategy_version",
        "_local_strategy_cache_version",
    ]

    class XTDevicePreference(StrEnum):
        IS_A_COVER_DEVICE = "IS_A_COVER_DEVICE"
        LOCK_MANUAL_UNLOCK_COMMAND = "LOCK_MANUAL_UNLOCK_COMMAND"
        LOCK_GET_SUPPORTED_UNLOCK_TYPES = "LOCK_GET_SUPPORTED_UNLOCK_TYPES"
        LOCK_GET_DOOR_LOCK_PASSWORD_TICKET = "LOCK_GET_DOOR_LOCK_PASSWORD_TICKET"
        LOCK_CALL_DOOR_OPERATE = "LOCK_CALL_DOOR_OPERATE"
        LOCK_CALL_DOOR_OPEN = "LOCK_CALL_DOOR_OPEN"
        HANDLED_DPCODES = "HANDLED_DPCODES"

    def __init__(self, **kwargs: Any) -> None:
        self.id: str = ""
        self.source = ""
        self.online_states = {}
        self.data_model = {}
        self.force_open_api = False
        self.name: str = ""
        self.local_key: str = ""
        self.category: str = ""
        self.product_id: str = ""
        self.product_name: str = ""
        self.sub: bool = False
        self.uuid: str = ""
        self.asset_id: str = ""
        self.online: bool = False
        self.icon: str = ""
        self.ip: str = ""
        self.time_zone: str = ""
        self.active_time = 0
        self.create_time = 0
        self.update_time = 0
        self.set_up: bool | None = False
        self.support_local: bool | None = False

        self._local_strategy_version = 0
        self._local_strategy_cache_version = -1
        self.code_to_dpid = {}
        self.dpid_to_code = {}
        self.local_strategy = {}
        self.status = {}
        self.function = {}  # type: ignore
        self.status_range = {}  # type: ignore
        self.device_preference = {}
        self.device_map: XTDeviceMap | None = None
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        function_str = "Functions:\r\n"
        for function in self.function.values():
            function_str += f"{function}\r\n"
        status_range_str = "StatusRange:\r\n"
        for status_range in self.status_range.values():
            status_range_str += f"{status_range}\r\n"
        status_str = "Status:\r\n"
        for code in self.status:
            status_str += f"{code}: {self.status[code]}\r\n"
        local_strategy_str = "LocalStrategy:\r\n"
        for dpId in self.local_strategy:
            local_strategy_str += f"{dpId}\r\n{self.local_strategy[dpId]}\r\n"

        return f"Device {self.name}:\r\n{function_str}{status_range_str}{status_str}{local_strategy_str}"
        # return f"Device {self.name}:\r\n{self.source}"

    @property
    def local_strategy(self) -> LocalStrategyDict:  # type: ignore[override]
        return self._local_strategy

    @local_strategy.setter
    def local_strategy(self, value: dict[int, dict[str, Any]] | LocalStrategyDict):  # type: ignore[override]
        if not isinstance(value, LocalStrategyDict):
            value = LocalStrategyDict(self, value)
        self._local_strategy = value
        self._invalidate_local_strategy_cache()
        self._refresh_local_strategy_cache()

    def _invalidate_local_strategy_cache(self) -> None:
        self._local_strategy_version += 1

    def _refresh_local_strategy_cache(self) -> None:
        self.code_to_dpid = {}
        self.dpid_to_code = {}
        for dp_id, item in self.local_strategy.items():
            code = item.get("status_code")
            if code:
                self.dpid_to_code[dp_id] = code
                self.code_to_dpid[code] = dp_id
                for alias in item.get("status_code_alias", []):
                    self.code_to_dpid[alias] = dp_id
        self._local_strategy_cache_version = self._local_strategy_version
    
    def set_device_map(self, device_map: XTDeviceMap):
        self.device_map = device_map

    def __setattr__(self, attr, value):
        super().__setattr__(attr, value)
        if attr not in XTDevice.FIELDS_TO_EXCLUDE_FROM_SYNC:
            if self.original_device is not None and hasattr(self.original_device, attr) and getattr(self.original_device, attr) != value:
                setattr(self.original_device, attr, value)
            XTDeviceMap.set_device_key_value_multimap(self.id, attr, value)

    @staticmethod
    def from_compatible_device(
        device: Any,
        source: str = "Compatible device",
        device_source_priority: int | None = None,
        keep_synced_with_original: bool = False
    ):
        # If the device is already an XT device return it right away
        if isinstance(device, XTDevice):
            return device

        new_device = XTDevice(**device.__dict__)
        new_device.source = source
        new_device.device_source_priority = device_source_priority
        if keep_synced_with_original:
            new_device.original_device = device

        # Reuse the references from the original device
        if hasattr(device, "local_strategy"):
            new_device.local_strategy = device.local_strategy
        if hasattr(device, "status"):
            new_device.status = device.status
        if hasattr(device, "function"):
            new_device.function = device.function
        if hasattr(device, "status_range"):
            new_device.status_range = device.status_range

        return new_device

    """def copy_data_from_device(source_device, dest_device) -> None:
        if hasattr(source_device, "online") and hasattr(dest_device, "online"):
            dest_device.online = source_device.online
        if hasattr(source_device, "name") and hasattr(dest_device, "name"):
            dest_device.name = source_device.name
        if hasattr(source_device, "status") and hasattr(dest_device, "status"):
            for code, value in source_device.status.items():
                dest_device.status[code] = value"""

    def get_copy(self) -> XTDevice:
        return copy.deepcopy(self)

    def get_multi_manager(self, hass: HomeAssistant) -> mm.MultiManager | None:
        return util.get_device_multi_manager(hass=hass, device=self)

    def get_preference(
        self, pref_id: str, ret_val_if_missing: Any | None = None
    ) -> Any | None:
        return self.device_preference.get(pref_id, ret_val_if_missing)

    def set_preference(self, pref_id: str, pref_val: Any):
        self.device_preference[pref_id] = pref_val

    def get_all_status_code_aliases(self) -> dict[str, str]:
        return_list: dict[str, str] = {}
        for local_strategy in self.local_strategy.values():
            if status_code := local_strategy.get("status_code", None):
                for alias in local_strategy.get("status_code_alias", {}):
                    return_list[alias] = status_code
        return return_list

    def replace_status_with_another(self, orig_status: str, new_status: str):
        # LOGGER.debug(f"Replacing {orig_status} with {new_status} in {device.name}")
        if orig_status in self.status_range:
            self.status_range[new_status] = self.status_range.pop(orig_status)
            self.status_range[new_status].code = new_status

        if orig_status in self.function:
            self.function[new_status] = self.function.pop(orig_status)
            self.function[new_status].code = new_status

        if orig_status in self.status:
            self.status[new_status] = self.status.pop(orig_status)

        for dpId in self.local_strategy:
            status_code = self.local_strategy[dpId].get("status_code")
            status_alias: list = self.local_strategy[dpId].get("status_code_alias", [])
            if status_code == orig_status:
                self.local_strategy[dpId]["status_code"] = new_status
                if new_status in status_alias:
                    status_alias.remove(new_status)
                if orig_status not in status_alias:
                    status_alias.append(orig_status)
                self.local_strategy[dpId]["status_code_alias"] = status_alias
                if config_item := self.local_strategy[dpId].get("config_item", None):
                    if status_formats := config_item.get("statusFormat", None):
                        status_formats_dict: dict = json.loads(status_formats)
                        for first_key in status_formats_dict:
                            status_formats_dict[new_status] = status_formats_dict.pop(
                                first_key
                            )
                            break
                        config_item["statusFormat"] = json.dumps(status_formats_dict)
                break


class XTDeviceMap(UserDict[str, XTDevice]):

    device_source_priority: XTDeviceSourcePriority | None = None
    master_device_map: list[XTDeviceMap] = []

    def __init__(
        self, iterable, device_source_priority: XTDeviceSourcePriority | None = None
    ):
        super().__init__(**iterable)
        self.device_source_priority = device_source_priority
        for device in self.values():
            device.set_device_map(self)

    @staticmethod
    def clear_master_device_map():
        XTDeviceMap.master_device_map = []

    @staticmethod
    def register_device_map(device_map: XTDeviceMap):
        if device_map not in XTDeviceMap.master_device_map:
            XTDeviceMap.master_device_map.append(device_map)
    
    @staticmethod
    def unregister_device_map(device_map: XTDeviceMap):
        if device_map in XTDeviceMap.master_device_map:
            XTDeviceMap.master_device_map.remove(device_map)
    
    @staticmethod
    def set_device_key_value_multimap(device_id: str, key: str, value: Any):
        if key in XTDevice.FIELDS_TO_EXCLUDE_FROM_SYNC:
            return None
        for device_map in XTDeviceMap.master_device_map:
            device_map.set_device_key_value(device_id, key, value)

    def set_device_key_value(self, device_id: str, key: str, value: Any):
        if key in XTDevice.FIELDS_TO_EXCLUDE_FROM_SYNC:
            return None
        if device := self.get(device_id):
            if hasattr(device, key) and getattr(device, key) != value:
                setattr(device, key, value)
