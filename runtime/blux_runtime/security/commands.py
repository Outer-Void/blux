"""Dangerous shell command detection (Stage 13, slice 5; parity matrix BLH-007).

This is a clean-room BLUX rule set. It must flag everything the BLHermes approval_detection golden flags,
and it is deliberately stricter in three places, where it also flags power state changes, killing init
and bootloader unlocks (see tests/core/test_security.py). Device rules (`device: True`) cover Android
bootloader, flash and partition operations and similar. Session approvals and YOLO can never cover them
(ADR-0012 and ADR-0013).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

MAX_COMMAND_CHARS = 16384


@dataclass(frozen=True)
class Rule:
    key: str
    description: str
    pattern: re.Pattern[str]
    device: bool = False


def _r(key: str, description: str, pattern: str, *, device: bool = False) -> Rule:
    return Rule(key, description, re.compile(pattern, re.I | re.S), device)


ROOTISH = (
    r"(?:/|/\*|~|~/|\$home|/home|/root|/etc|/usr|/var|/boot|/bin|/sbin|/lib|/opt|/sdcard|/storage|/data)"
    r"(?:/?\s|/?$|/?;|/?&)"
)
RULES: tuple[Rule, ...] = (
    _r(
        "device.bootloader",
        "bootloader lock state change",
        r"\b(?:fastboot|fastbootd)\b.*\b(?:flashing\s+(?:unlock|lock)|oem\s+(?:unlock|lock))",
        device=True,
    ),
    _r(
        "device.flash",
        "flash, erase or format a device partition",
        r"\bfastboot\b.*\b(?:flash|erase|format|wipe|update|boot)\b|\b(?:heimdall|odin)\b|\badb\b.*\b(?:sideload|reboot\s+(?:bootloader|recovery|fastboot|edl))\b",
        device=True,
    ),
    _r(
        "device.adb-destructive",
        "destructive command on an attached Android device",
        # `wipe\w*` also catches `--wipe_data`/`wipe_cache` ("_" is a word character, so `\bwipe\b` missed them;
        # found by the Stage 35 Generation 0 policy suite).
        r"\badb\b.*\bshell\b.*\b(?:rm|dd|mkfs|wipe\w*|format|pm\s+(?:uninstall|clear)|setprop|reboot)\b",
        device=True,
    ),
    _r(
        "delete.root",
        "recursive delete of a root or home path",
        r"\brm\s+(?:-[a-z]*\s+)*-[a-z]*r[a-z]*\s+(?:-[a-z]*\s+)*" + ROOTISH,
    ),
    _r("delete.recursive", "recursive delete", r"\brm\s+(?:-[a-z]*\s+)*(?:-[a-z]*r[a-z]*|--recursive)\b"),
    _r("delete.find", "find with -delete or -exec rm", r"\bfind\b.*(?:-delete\b|-exec\s+rm\b)"),
    _r("delete.shred", "irreversible overwrite", r"\b(?:shred|wipefs|srm)\b"),
    _r("disk.write", "raw write to a block device", r"\bdd\b.*\bof=/dev/|>\s*/dev/(?:sd|nvme|mmcblk|disk|block)"),
    _r(
        "disk.format",
        "filesystem or partition table change",
        r"\b(?:mkfs(?:\.\w+)?|fdisk|sfdisk|parted|gdisk|mkswap)\b",
    ),
    _r(
        "perm.world",
        "recursive permission or owner change",
        r"\b(?:chmod|chown|chgrp)\s+(?:-[a-z]*\s+)*-[a-z]*r[a-z]*\b|\bchmod\s+(?:-\w+\s+)*0?777\b",
    ),
    _r("fork.bomb", "fork bomb", r":\s*\(\s*\)\s*\{.*:\s*\|\s*:.*&.*\}"),
    _r(
        "pipe.shell",
        "download piped into an interpreter",
        r"\b(?:curl|wget|fetch)\b[^|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|python\d*|perl|ruby|node)\b",
    ),
    _r(
        "git.force",
        "history-rewriting git command",
        r"\bgit\b.*\b(?:push\b.*(?:--force\b|--force-with-lease\b|\s-f\b|\+\w)|reset\s+--hard\b|clean\s+-[a-z]*f|branch\s+-D\b|filter-branch\b|checkout\s+--\s+\.)",
    ),
    _r(
        "system.write",
        "write into a system path",
        r"(?:>>?|\btee\b(?:\s+-a)?)\s*/(?:etc|boot|usr|bin|sbin|lib|sys|proc)/",
    ),
    _r(
        "system.power",
        "power state change",
        r"(?:^|[;&|]\s*|\bsudo\s+)(?:shutdown|reboot|halt|poweroff)\b|\binit\s+[06]\b|\bsystemctl\s+(?:poweroff|reboot|halt)\b",
    ),
    _r(
        "process.init",
        "signal to init or every process",
        r"\bkill\s+(?:-\w+\s+)*-?(?:1|-1)\b(?!\d)|\bkillall\b|\bpkill\s+-9\b",
    ),
    _r("privilege", "privileged execution", r"(?:^|[;&|]\s*)(?:sudo|su|doas|pkexec)\b"),
    _r(
        "code.inline",
        "inline code execution",
        r"\b(?:python\d*(?:\.\d+)?|perl|ruby|node|php|bash|sh|zsh)\s+(?:-\w*\s+)*-[ce]\b|\beval\b|\bsource\s+/dev/stdin",
    ),
    _r("parser.limit", "command too long to analyse", r"(?!x)x"),
)


@dataclass(frozen=True)
class Detection:
    dangerous: bool
    rule: str | None
    description: str | None
    device: bool = False


SAFE = Detection(False, None, None, False)


def detect(command: str) -> Detection:
    """Classify one shell command line. Unparseable or oversized input is treated as dangerous."""
    if len(command) > MAX_COMMAND_CHARS:
        return Detection(True, "parser.limit", "command too long to analyse")
    try:
        shlex.split(command, posix=True)
    except ValueError:
        return Detection(True, "parser.unbalanced", "unbalanced quoting")
    text = " ".join(command.split())
    for rule in RULES:
        if rule.pattern.search(text):
            return Detection(True, rule.key, rule.description, rule.device)
    return SAFE
