import sys
from pathlib import Path
import pytest

# Add the project root to the path to allow direct imports from 'custom_components'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.xtend_tuya.multi_manager.shared.merging_manager import XTMergingManager

def test_smart_merge_sets():
    """Test that smart_merge correctly merges two sets."""
    set1 = {1, 2, 3}
    set2 = {3, 4, 5}
    expected = {1, 2, 3, 4, 5}
    result = XTMergingManager.smart_merge(set1, set2)
    assert result == expected
