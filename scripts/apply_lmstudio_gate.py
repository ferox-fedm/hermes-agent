#!/usr/bin/env python3
"""
Re-apply LM Studio single-model gate patches after hermes update.

This script re-applies the patches that enable the LM Studio single-model gate
feature. Run this after `hermes update` to restore the patches.

Usage:
    python apply_lmstudio_gate.py
"""

import os
import sys
import shutil
from pathlib import Path

# Paths
HERMES_HOME = Path(os.getenv("HERMES_HOME", r"G:\Hermes"))
HERMES_AGENT = HERMES_HOME / "hermes-agent"
AGENT_DIR = HERMES_AGENT / "agent"
BACKUP_DIR = HERMES_HOME / "lmstudio_gate_backup"

# Source files (from backup)
LMSTUDIO_SINGLE_MODEL_SRC = BACKUP_DIR / "lmstudio_single_model.py"
CHAT_COMPLETION_HELPERS_SRC = BACKUP_DIR / "chat_completion_helpers.py"
AUXILIARY_CLIENT_SRC = BACKUP_DIR / "auxiliary_client.py"
RUN_AGENT_SRC = BACKUP_DIR / "run_agent.py"

# Target files
LMSTUDIO_SINGLE_MODEL_DST = AGENT_DIR / "lmstudio_single_model.py"
CHAT_COMPLETION_HELPERS_DST = AGENT_DIR / "chat_completion_helpers.py"
AUXILIARY_CLIENT_DST = AGENT_DIR / "auxiliary_client.py"
RUN_AGENT_DST = HERMES_AGENT / "run_agent.py"


def check_backup_exists():
    """Check if backup files exist."""
    files = [
        LMSTUDIO_SINGLE_MODEL_SRC,
        CHAT_COMPLETION_HELPERS_SRC,
        AUXILIARY_CLIENT_SRC,
        RUN_AGENT_SRC,
    ]
    missing = [f for f in files if not f.exists()]
    if missing:
        print("ERROR: Backup files not found:")
        for f in missing:
            print(f"  - {f}")
        print("\nPlease ensure backup files are in:")
        print(f"  {BACKUP_DIR}")
        return False
    return True


def backup_current_files():
    """Backup current files before overwriting."""
    print("Backing up current files...")
    BACKUP_DIR.mkdir(exist_ok=True)
    
    files_to_backup = [
        (LMSTUDIO_SINGLE_MODEL_DST, BACKUP_DIR / "lmstudio_single_model.py.bak"),
        (CHAT_COMPLETION_HELPERS_DST, BACKUP_DIR / "chat_completion_helpers.py.bak"),
        (AUXILIARY_CLIENT_DST, BACKUP_DIR / "auxiliary_client.py.bak"),
        (RUN_AGENT_DST, BACKUP_DIR / "run_agent.py.bak"),
    ]
    
    for src, dst in files_to_backup:
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Backed up: {src.name}")
        else:
            print(f"  Skipped (not found): {src.name}")


def apply_patches():
    """Apply the patches."""
    print("\nApplying patches...")
    
    # Copy lmstudio_single_model.py
    if LMSTUDIO_SINGLE_MODEL_SRC.exists():
        shutil.copy2(LMSTUDIO_SINGLE_MODEL_SRC, LMSTUDIO_SINGLE_MODEL_DST)
        print(f"  Copied: lmstudio_single_model.py")
    else:
        print(f"  ERROR: {LMSTUDIO_SINGLE_MODEL_SRC} not found")
        return False
    
    # Copy modified files
    files_to_copy = [
        (CHAT_COMPLETION_HELPERS_SRC, CHAT_COMPLETION_HELPERS_DST),
        (AUXILIARY_CLIENT_SRC, AUXILIARY_CLIENT_DST),
        (RUN_AGENT_SRC, RUN_AGENT_DST),
    ]
    
    for src, dst in files_to_copy:
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Copied: {dst.name}")
        else:
            print(f"  ERROR: {src} not found")
            return False
    
    return True


def verify_patches():
    """Verify patches were applied correctly."""
    print("\nVerifying patches...")
    
    # Check lmstudio_single_model.py exists
    if not LMSTUDIO_SINGLE_MODEL_DST.exists():
        print("  ERROR: lmstudio_single_model.py not found")
        return False
    
    # Check chat_completion_helpers.py has the import
    with open(CHAT_COMPLETION_HELPERS_DST, "r", encoding="utf-8") as f:
        content = f.read()
        if "from agent.lmstudio_single_model import create_chat_completion_with_lmstudio_gate" not in content:
            print("  ERROR: chat_completion_helpers.py missing import")
            return False
        if "create_chat_completion_with_lmstudio_gate(" not in content:
            print("  ERROR: chat_completion_helpers.py missing gate calls")
            return False
    
    # Check auxiliary_client.py has the imports
    with open(AUXILIARY_CLIENT_DST, "r", encoding="utf-8") as f:
        content = f.read()
        if "from agent.lmstudio_single_model import (" not in content:
            print("  ERROR: auxiliary_client.py missing imports")
            return False
        if "create_chat_completion_with_lmstudio_gate(" not in content:
            print("  ERROR: auxiliary_client.py missing gate calls")
            return False
    
    # Check run_agent.py has HERMES_LMSTUDIO_PRELOAD gating
    with open(RUN_AGENT_DST, "r", encoding="utf-8") as f:
        content = f.read()
        if "HERMES_LMSTUDIO_PRELOAD" not in content:
            print("  WARNING: run_agent.py missing HERMES_LMSTUDIO_PRELOAD gating")
    
    print("  All patches verified successfully!")
    return True


def main():
    """Main function."""
    print("=" * 60)
    print("LM Studio Single-Model Gate - Patch Re-Apply Script")
    print("=" * 60)
    
    # Check backup exists
    if not check_backup_exists():
        print("\nPlease restore backup files and try again.")
        sys.exit(1)
    
    # Backup current files
    backup_current_files()
    
    # Apply patches
    if not apply_patches():
        print("\nFailed to apply patches. Check error messages above.")
        sys.exit(1)
    
    # Verify patches
    if not verify_patches():
        print("\nPatch verification failed. Check error messages above.")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("SUCCESS! Patches applied successfully.")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Restart hermes gateway")
    print("2. Test vision analysis to verify the gate works")
    print("\nEnvironment variables to set:")
    print("  set HERMES_LMSTUDIO_SINGLE_MODEL=1")
    print("  set HERMES_LMSTUDIO_PRELOAD=0")


if __name__ == "__main__":
    main()
