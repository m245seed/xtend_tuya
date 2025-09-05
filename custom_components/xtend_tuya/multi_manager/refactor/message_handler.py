from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from tuya_iot.device import (
    PROTOCOL_DEVICE_REPORT,
    PROTOCOL_OTHER,
)

from ...const import LOGGER

if TYPE_CHECKING:
    from ..multi_manager import MultiManager
    from ..shared.shared_classes import XTDevice


class MessageHandler:
    """A helper class to handle messages from Tuya devices."""

    def __init__(self, multi_manager: MultiManager):
        """Initialize the message handler."""
        self.multi_manager = multi_manager

    def on_message(self, source: str, msg: dict):
        """Handle an incoming message from a Tuya device."""
        if not self.multi_manager.is_ready_for_messages:
            self.multi_manager.pending_messages.append((source, msg))
            return
        dev_id = self._get_device_id_from_message(msg)
        if not dev_id:
            LOGGER.warning(f"dev_id {dev_id} not found!")
            return

        new_message = self._convert_message_for_all_accounts(msg)
        self.multi_manager.device_watcher.report_message(
            dev_id, f"on_message ({source}) => {msg} <=> {new_message}"
        )
        if status_list := self._get_status_list_from_message(msg):
            self.multi_manager.device_watcher.report_message(
                dev_id, f"On Message reporting ({source}): {msg}"
            )
            self.multi_manager.multi_source_handler.register_status_list_from_source(
                dev_id, source, status_list
            )

        if source in self.multi_manager.accounts:
            self.multi_manager.accounts[source].on_message(new_message)

    def _get_device_id_from_message(self, msg: dict) -> str | None:
        """Extract the device ID from a message."""
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if dev_id := data.get("devId", None):
            return dev_id
        if protocol == PROTOCOL_OTHER:
            if bizData := data.get("bizData", None):
                if dev_id := bizData.get("devId", None):
                    return dev_id
        return None

    def _get_status_list_from_message(self, msg: dict) -> str | None:
        """Extract the status list from a message."""
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if protocol == PROTOCOL_DEVICE_REPORT and "status" in data:
            return data["status"]
        return None

    def _convert_message_for_all_accounts(self, msg: dict) -> dict:
        """Convert a message to a format that all accounts can understand."""
        protocol = msg.get("protocol", 0)
        data = msg.get("data", {})
        if protocol == PROTOCOL_DEVICE_REPORT:
            return msg
        elif protocol == PROTOCOL_OTHER:
            if hasattr(data, "devId"):
                return msg
            else:
                if bizData := data.get("bizData", None):
                    if dev_id := bizData.get("devId", None):
                        data["devId"] = dev_id
        return msg

    def convert_device_report_status_list(
        self, device_id: str, status_in: list
    ) -> list[dict[str, Any]]:
        status = copy.deepcopy(status_in)
        for item in status:
            code, dpId, value, result_ok = self._read_code_dpid_value_from_state(
                device_id, item
            )
            if result_ok:
                item["code"] = code
                item["dpId"] = dpId
                item["value"] = value
            else:
                pass
        return status

    def _read_code_dpid_value_from_state(
        self,
        device_id: str,
        state,
        fail_if_dpid_not_found=True,
        fail_if_code_not_found=True,
    ):
        device = self.multi_manager.device_map.get(device_id)
        if not device:
            success = not (
                fail_if_code_not_found or fail_if_dpid_not_found
            )
            if not success:
                return None, None, None, False
            return None, None, state.get("value"), True

        code, dp_id, value = self._extract_initial_state(state)
        code, dp_id = self._resolve_code_and_dpid(code, dp_id, device)

        if dp_id is not None:
            code = self._resolve_code_alias(dp_id, code, device)

        if code is None and dp_id is None:
            code, dp_id, value = self._find_code_dpid_from_state_items(
                state, device, value
            )

        if code is not None and dp_id is not None:
            return code, dp_id, value, True

        success = not (
            (code is None and fail_if_code_not_found)
            or (dp_id is None and fail_if_dpid_not_found)
        )

        if not success:
            return None, None, None, False

        return code, dp_id, value, True

    def _extract_initial_state(self, state):
        """Extract the initial code, dpId, and value from the state."""
        code = state.get("code")
        dp_id = state.get("dpId")
        value = state.get("value")
        return code, dp_id, value

    def _resolve_code_and_dpid(self, code, dp_id, device):
        """Resolve code from dpId and vice-versa."""
        if code is None and dp_id is not None:
            code = self.multi_manager._read_code_from_dpId(dp_id, device)
        elif dp_id is None and code is not None:
            dp_id = self.multi_manager._read_dpId_from_code(code, device)
        return code, dp_id

    def _resolve_code_alias(self, dp_id, code, device):
        """Resolve any alias code to the main code for a given dpId."""
        code_non_alias = self.multi_manager._read_code_from_dpId(dp_id, device)
        return code_non_alias if code_non_alias is not None else code

    def _find_code_dpid_from_state_items(self, state, device, original_value):
        """Find code and dpId by iterating through the state items."""
        for temp_dp_id, temp_value in state.items():
            try:
                dp_id_int = int(temp_dp_id)
                temp_code = self.multi_manager._read_code_from_dpId(dp_id_int, device)
                if temp_code is not None:
                    return temp_code, dp_id_int, temp_value
            except (ValueError, TypeError):
                continue
        return None, None, original_value
