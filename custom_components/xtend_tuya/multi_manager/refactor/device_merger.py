from __future__ import annotations

from typing import TYPE_CHECKING

from ..shared.merging_manager import XTMergingManager
from ..shared.shared_classes import XTDevice

if TYPE_CHECKING:
    from ..multi_manager import MultiManager


class DeviceMerger:
    """A helper class to merge device information from multiple sources."""

    def __init__(self, multi_manager: MultiManager):
        self.multi_manager = multi_manager

    def merge_devices(self):
        """Merge devices from multiple sources into a single device."""
        for device in self.multi_manager.device_map.values():
            to_be_merged: list[XTDevice] = []
            devices = self._get_devices_from_device_id(device.id)
            for current_device in devices:
                for prev_device in to_be_merged:
                    XTMergingManager.merge_devices(
                        prev_device, current_device, self.multi_manager
                    )
                to_be_merged.append(current_device)

    def _get_devices_from_device_id(self, device_id: str) -> list[XTDevice]:
        """Get all device instances for a given device ID."""
        return_list = []
        device_maps = self.multi_manager.get_available_device_maps()
        for device_map in device_maps:
            if device_id in device_map:
                return_list.append(device_map[device_id])
        return return_list
