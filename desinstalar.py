#!/usr/bin/env python3
"""
CleanUninstall - full uninstallation and orphaned file cleanup.

Python version of the workflow originally implemented in desinstalar.sh.
"""

from __future__ import annotations

import argparse
import curses
import os
import re
import shutil
import subprocess
import sys
import gzip
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PACKAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.+:_@-]*$")
SYSTEM_CONFIG_NAMES = {
    "dconf",
    "fontconfig",
    "pulse",
    "pki",
    "ibus",
    "gtk-2.0",
    "gtk-3.0",
    "gtk-4.0",
    "mimeapps",
    "user-dirs",
    "trolltech",
    "qt5ct",
    "qt6ct",
    "kdeglobals",
    "kconf",
    "baloo",
    "systemd",
    "networkmanager",
    "wpa_supplicant",
    "ca-certificates",
    "ssl",
    "gnupg",
    "ssh",
    "autostart",
    "environment.d",
    "sysctl.d",
    "ld.so.conf.d",
    "x11",
    "xorg",
    "xkb",
    "xinit",
    "apparmor",
    "selinux",
    "polkit",
    "udisks2",
    "udev",
    "modprobe.d",
    "initramfs-tools",
    "ldconfig",
    "alternatives",
    "dpkg",
    "apt",
    "bash-completion",
    "profile.d",
    ".fonts",
    ".icons",
    ".themes",
}
SYSTEM_CONFIG_PREFIXES = ("plasma", "kde", "user-dirs.")
GENERIC_CONFIG_ENTRIES = {
    "emaildefaults",
    "mimeapps.list",
    "qtproject.conf",
    "trolltech.conf",
    "electron-flags.conf",
}
APP_ALIAS_MAP = {
    "bravesoftware": {"brave-browser", "brave"},
    "google-chrome-for-testing": {"google-chrome", "google-chrome-stable"},
    "googlechromefortesting": {"google-chrome", "google-chrome-stable"},
    "lm studio": {"lm-studio"},
    "lm-studio": {"lm-studio"},
    "qterminal.org": {"qterminal"},
    "qterminalorg": {"qterminal"},
}
USER_APP_BLACKLIST = {
    "baobab",
    "celluloid",
    "gparted",
    "gucharmap",
    "hypnotix",
    "info",
    "lightdm-settings",
    "mintlocale",
    "mintupgrade",
    "sticky",
    "thunderbird",
    "timeshift",
    "transmission-gtk",
    "vlc",
    "warpinator",
    "webapp-manager",
    "xed",
    "xviewer",
}
PROTECTED_PATHS = {
    Path("/"),
    Path("/home"),
    Path("/root"),
    Path("/etc"),
    Path("/usr"),
    Path("/var"),
}


class Color:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
    WHITE = "\033[1;37m"
    NC = "\033[0m"


def ctext(color: str, text: str) -> str:
    return f"{color}{text}{Color.NC}"


def info(message: str) -> None:
    print(f"{ctext(Color.BLUE, '[INFO]')} {message}")


def success(message: str) -> None:
    print(f"{ctext(Color.GREEN, '[✓]')} {message}")


def warn(message: str) -> None:
    print(f"{ctext(Color.YELLOW, '[⚠]')} {message}")


def error(message: str) -> None:
    print(f"{ctext(Color.RED, '[✗]')} {message}", file=sys.stderr)


@dataclass
class AppEntry:
    package: str
    description: str
    size: str
    config_path: Path
    user: str


@dataclass
class OrphanEntry:
    name: str
    path: Path
    user: str
    size: str
    entry_type: str


@dataclass
class PackageManager:
    name: str
    remove_cmd: list[str]
    autoremove_cmd: list[str]
    clean_cmd: list[str]


@dataclass
class UserInstalledApp:
    package: str
    display_name: str
    description: str
    size: str


class CleanUninstallApp:
    def __init__(self, auto_yes: bool = False) -> None:
        self.auto_yes = auto_yes
        self.pkg_manager = self.detect_pkg_manager()
        self.installed_packages = self.load_installed_packages()
        self.available_commands = self.load_available_commands()

    def print_banner(self) -> None:
        print(ctext(Color.CYAN, ""))
        print("  ╔══════════════════════════════════════════════════════════╗")
        print("  ║            CleanUninstall - Full Removal                ║")
        print("  ║      Remove apps and clean orphaned leftovers           ║")
        print("  ╚══════════════════════════════════════════════════════════╝")
        print(ctext(Color.CYAN, "") + Color.NC)

    def run_command(
        self,
        cmd: list[str],
        *,
        capture: bool = False,
        check: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=capture,
            check=check,
            text=text,
        )

    def detect_pkg_manager(self) -> PackageManager:
        if shutil.which("apt"):
            manager = PackageManager(
                name="apt",
                remove_cmd=["apt", "purge", "-y"],
                autoremove_cmd=["apt", "autoremove", "--purge", "-y"],
                clean_cmd=["apt", "autoclean", "-y"],
            )
        elif shutil.which("dnf"):
            manager = PackageManager(
                name="dnf",
                remove_cmd=["dnf", "remove", "-y"],
                autoremove_cmd=["dnf", "autoremove", "-y"],
                clean_cmd=["dnf", "clean", "all"],
            )
        elif shutil.which("pacman"):
            manager = PackageManager(
                name="pacman",
                remove_cmd=["pacman", "-Rns", "--noconfirm"],
                autoremove_cmd=["pacman", "-Rns", "--noconfirm"],
                clean_cmd=["pacman", "-Sc", "--noconfirm"],
            )
        else:
            raise SystemExit("No supported package manager was detected.")

        success(f"Package manager: {manager.name}")
        return manager

    def load_installed_packages(self) -> set[str]:
        info("Loading installed package list...")
        packages: set[str] = set()

        if self.pkg_manager.name == "apt":
            result = self.run_command(
                ["dpkg-query", "-W", "-f=${Package}\n"], capture=True
            )
            packages = {line.strip().lower() for line in result.stdout.splitlines() if line.strip()}
        elif self.pkg_manager.name == "pacman":
            result = self.run_command(["pacman", "-Qq"], capture=True)
            packages = {line.strip().lower() for line in result.stdout.splitlines() if line.strip()}
        elif self.pkg_manager.name == "dnf":
            result = self.run_command(["dnf", "list", "installed", "--quiet"], capture=True)
            for line in result.stdout.splitlines()[1:]:
                token = line.split()[0] if line.split() else ""
                if token:
                    packages.add(token.split(".")[0].lower())

        success(f"Loaded packages: {len(packages)}")
        return packages

    def load_available_commands(self) -> set[str]:
        result = self.run_command(["bash", "-lc", "compgen -c"], capture=True)
        commands = {line.strip().lower() for line in result.stdout.splitlines() if line.strip()}
        return commands

    def command_output_lines(self, cmd: list[str]) -> list[str]:
        result = self.run_command(cmd, capture=True)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def confirm(self, prompt: str) -> bool:
        if self.auto_yes:
            info(f"{prompt} -> yes")
            return True
        reply = input(f"{prompt} (y/N): ").strip().lower()
        return reply == "y"

    def normalize_name(self, name: str) -> str:
        return name.lstrip(".").lower()

    def base_name_without_suffix(self, name: str) -> str:
        lower = self.normalize_name(name)
        for suffix in (".conf", ".list", ".desktop", ".org"):
            if lower.endswith(suffix):
                return lower[: -len(suffix)]
        return lower

    def candidate_names(self, raw_name: str) -> set[str]:
        original = self.normalize_name(raw_name)
        base = self.base_name_without_suffix(raw_name)
        seeds = {original, base}
        for item in list(seeds):
            seeds.add(item.replace("_", "-"))
            seeds.add(item.replace("-", "_"))
            seeds.add(item.replace(" ", "-"))
            seeds.add(item.replace(" ", ""))
            seeds.add(item.replace(".", "-"))
            seeds.add(item.replace(".", ""))
        expanded = set(seeds)
        for item in list(seeds):
            expanded.update(APP_ALIAS_MAP.get(item, set()))
            expanded.update(APP_ALIAS_MAP.get(item.replace("-", ""), set()))
        cleaned = {item.strip("-_. ") for item in expanded if item.strip("-_. ")}
        final = set(cleaned)
        for item in cleaned:
            if item.endswith("software") and len(item) > len("software"):
                final.add(item[: -len("software")].rstrip("-_. "))
        return {item for item in final if item}

    def is_safe_package_name(self, package: str) -> bool:
        return bool(PACKAGE_RE.match(package))

    def is_package_installed(self, name: str) -> bool:
        candidates = self.candidate_names(name)
        for candidate in candidates:
            if candidate in self.installed_packages or candidate in self.available_commands:
                return True

        for candidate in candidates:
            for pkg in self.installed_packages:
                if candidate == pkg:
                    return True
                if candidate.startswith(f"{pkg}-") or pkg.startswith(f"{candidate}-"):
                    return True
                if len(candidate) > 3 and (candidate in pkg or pkg in candidate):
                    return True
        return False

    def resolve_installed_package(self, raw_name: str) -> str | None:
        candidates = self.candidate_names(raw_name)
        if not candidates:
            return None

        for candidate in candidates:
            if candidate in self.installed_packages:
                return candidate

        partial_matches = sorted(
            {
                pkg
                for candidate in candidates
                for pkg in self.installed_packages
                if pkg.startswith(f"{candidate}-")
                or candidate.startswith(f"{pkg}-")
                or (len(candidate) > 3 and candidate in pkg)
            }
        )
        return partial_matches[0] if len(partial_matches) == 1 else None

    def is_system_config(self, name: str) -> bool:
        lower = name.lower()
        if lower in SYSTEM_CONFIG_NAMES:
            return True
        if any(lower.startswith(prefix) for prefix in SYSTEM_CONFIG_PREFIXES):
            return True
        return len(lower) <= 2

    def is_generic_config_entry(self, name: str) -> bool:
        return self.normalize_name(name) in GENERIC_CONFIG_ENTRIES

    def config_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        home_root = Path("/home")
        if home_root.is_dir():
            for home_dir in sorted(home_root.iterdir()):
                config_dir = home_dir / ".config"
                if config_dir.is_dir():
                    dirs.append(config_dir)
        root_config = Path("/root/.config")
        if root_config.is_dir():
            dirs.append(root_config)
        return dirs

    def user_from_config_dir(self, config_dir: Path) -> str:
        if config_dir == Path("/root/.config"):
            return "root"
        return config_dir.parent.name

    def format_size_kb(self, size_kb: str) -> str:
        if not size_kb.isdigit():
            return "?"
        value = int(size_kb)
        if value >= 1024:
            return f"{value / 1024:.1f} MB"
        return f"{value} KB"

    def describe_package(self, package: str) -> tuple[str, str]:
        if self.pkg_manager.name == "apt":
            show = self.run_command(["apt-cache", "show", package], capture=True)
            description = "(no description)"
            for line in show.stdout.splitlines():
                if line.startswith("Description-en:"):
                    description = line.split(":", 1)[1].strip()[:80]
                    break
                if line.startswith("Description:") and description == "(no description)":
                    description = line.split(":", 1)[1].strip()[:80]
            size = self.run_command(
                ["dpkg-query", "-W", "-f=${Installed-Size}", package], capture=True
            ).stdout.strip()
            return description, self.format_size_kb(size)

        if self.pkg_manager.name == "pacman":
            result = self.run_command(["pacman", "-Qi", package], capture=True)
            description = "(no description)"
            size = "?"
            for line in result.stdout.splitlines():
                if line.startswith("Description"):
                    description = line.split(":", 1)[1].strip()[:80]
                if line.startswith("Installed Size"):
                    size = " ".join(line.split(":", 1)[1].strip().split()[:2])
            return description, size

        result = self.run_command(["dnf", "info", package], capture=True)
        description = "(no description)"
        size = "?"
        for line in result.stdout.splitlines():
            if line.startswith("Description"):
                description = line.split(":", 1)[1].strip()[:80]
            if line.startswith("Size"):
                size = " ".join(line.split(":", 1)[1].strip().split()[:2])
        return description, size

    def package_display_name_from_desktop(self, desktop_file: Path) -> str | None:
        try:
            for line in desktop_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Name="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            return None
        return None

    def apt_initial_system_packages(self) -> set[str]:
        candidates = [
            Path("/var/log/installer/initial-status.gz"),
            Path("/var/log/installer/status"),
        ]
        packages: set[str] = set()
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                if candidate.suffix == ".gz":
                    with gzip.open(candidate, "rt", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                else:
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            current_package: str | None = None
            for line in content.splitlines():
                if line.startswith("Package: "):
                    current_package = line.split(":", 1)[1].strip().lower()
                elif not line.strip() and current_package:
                    packages.add(current_package)
                    current_package = None
            if current_package:
                packages.add(current_package)
            if packages:
                break
        return packages

    def apt_manual_packages(self) -> set[str]:
        return set(self.command_output_lines(["apt-mark", "showmanual"]))

    def apt_history_log_files(self) -> list[Path]:
        paths = sorted(Path("/var/log/apt").glob("history.log*"))
        return paths

    def read_text_maybe_gzip(self, path: Path) -> str:
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def parse_apt_history_packages(self, line: str) -> list[tuple[str, bool]]:
        if ":" not in line:
            return []
        payload = line.split(":", 1)[1].strip()
        parts = [part.strip() for part in payload.split(", ") if part.strip()]
        parsed: list[tuple[str, bool]] = []
        for part in parts:
            name = part.split(":", 1)[0].strip().lower()
            automatic = "automatic" in part.lower()
            if name:
                parsed.append((name, automatic))
        return parsed

    def apt_explicitly_requested_packages(self) -> set[str]:
        requested: set[str] = set()
        for log_file in self.apt_history_log_files():
            content = self.read_text_maybe_gzip(log_file)
            if not content:
                continue
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if line.startswith("Install: "):
                    for package, automatic in self.parse_apt_history_packages(line):
                        if not automatic:
                            requested.add(package)
                elif line.startswith("Remove: ") or line.startswith("Purge: "):
                    for package, _ in self.parse_apt_history_packages(line):
                        requested.discard(package)
        return requested

    def apt_installed_meta_packages(self) -> list[str]:
        meta_candidates = [
            "mint-meta-cinnamon",
            "mint-meta-core",
            "ubuntu-desktop",
            "ubuntu-desktop-minimal",
            "xubuntu-desktop",
            "lubuntu-desktop",
            "kubuntu-desktop",
            "ubuntu-mate-desktop",
        ]
        return [package for package in meta_candidates if package in self.installed_packages]

    def apt_system_seed_packages(self) -> set[str]:
        meta_packages = self.apt_installed_meta_packages()
        if not meta_packages:
            return set()
        result = self.run_command(
            [
                "apt-cache",
                "depends",
                "--recurse",
                "--no-suggests",
                "--no-conflicts",
                "--no-breaks",
                "--no-replaces",
                "--no-enhances",
                *meta_packages,
            ],
            capture=True,
        )
        packages = set(meta_packages)
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("<"):
                continue
            if ":" in line:
                _, value = line.split(":", 1)
                name = value.strip()
            else:
                name = line
            name = name.split(":", 1)[0].strip().lower()
            if name and not name.startswith("<"):
                packages.add(name)
        return packages

    def package_owner_map_for_files(self, files: list[Path]) -> dict[Path, str]:
        owners: dict[Path, str] = {}
        if not files:
            return owners
        result = self.run_command(
            ["dpkg-query", "-S", *[str(path) for path in files]],
            capture=True,
        )
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            pkg_part, path_part = line.split(":", 1)
            path = Path(path_part.strip())
            packages = [pkg.strip() for pkg in pkg_part.split(",") if pkg.strip()]
            if packages:
                owners[path] = packages[0]
        return owners

    def desktop_application_files(self) -> list[Path]:
        roots = [
            Path("/usr/share/applications"),
            Path("/var/lib/snapd/desktop/applications"),
        ]
        files: list[Path] = []
        for root in roots:
            if root.is_dir():
                files.extend(sorted(root.glob("*.desktop")))
        return files

    def list_user_installed_apps(self) -> list[UserInstalledApp]:
        if self.pkg_manager.name != "apt":
            warn("The -p mode is optimized for apt-based systems. A basic detection mode will be used.")

        desktop_files = self.desktop_application_files()
        owner_map = self.package_owner_map_for_files(desktop_files)

        if self.pkg_manager.name == "apt":
            manual_packages = self.apt_manual_packages()
            initial_system = self.apt_initial_system_packages()
            explicit_requested = self.apt_explicitly_requested_packages()
            system_seed_packages = self.apt_system_seed_packages()
        else:
            manual_packages = set(self.installed_packages)
            initial_system = set()
            explicit_requested = set()
            system_seed_packages = set()

        candidates: list[tuple[Path, str]] = []
        for desktop_file in desktop_files:
            package = owner_map.get(desktop_file)
            if not package:
                continue
            package = package.lower()
            if package not in self.installed_packages:
                continue
            if package not in manual_packages:
                continue
            if package in initial_system:
                continue
            if package in system_seed_packages:
                continue
            if package in USER_APP_BLACKLIST:
                continue
            candidates.append((desktop_file, package))

        filtered_candidates = candidates
        if explicit_requested:
            explicit_only = [
                (desktop_file, package)
                for desktop_file, package in candidates
                if package in explicit_requested
            ]
            if explicit_only:
                filtered_candidates = explicit_only

        apps_by_package: dict[str, UserInstalledApp] = {}
        for desktop_file, package in filtered_candidates:
            display_name = self.package_display_name_from_desktop(desktop_file) or package
            description, size = self.describe_package(package)
            current = apps_by_package.get(package)
            if current is None or len(display_name) < len(current.display_name):
                apps_by_package[package] = UserInstalledApp(
                    package=package,
                    display_name=display_name,
                    description=description,
                    size=size,
                )

        apps = sorted(apps_by_package.values(), key=lambda app: (app.display_name.lower(), app.package))
        success(f"User-installed apps found: {len(apps)}")
        return apps

    def package_picker_ui(self, apps: list[UserInstalledApp]) -> list[UserInstalledApp]:
        if not apps:
            return []

        def _run(stdscr: curses.window) -> list[UserInstalledApp]:
            curses.curs_set(0)
            stdscr.keypad(True)
            selected: set[int] = set()
            current = 0
            scroll = 0
            message = "Space toggles, [d] removes selected, [q] exits"

            while True:
                stdscr.erase()
                height, width = stdscr.getmaxyx()
                visible_rows = max(5, height - 4)
                if current < scroll:
                    scroll = current
                if current >= scroll + visible_rows:
                    scroll = current - visible_rows + 1

                title = "User-installed applications"
                stdscr.addnstr(0, 0, title, width - 1, curses.A_BOLD)
                header = "Use arrow keys. Space toggles selection. [d] deletes. [q] cancels."
                stdscr.addnstr(1, 0, header, width - 1)

                for row, idx in enumerate(range(scroll, min(len(apps), scroll + visible_rows)), start=2):
                    app = apps[idx]
                    marker = "[x]" if idx in selected else "[ ]"
                    line = f"{marker} {app.display_name} [{app.package}] - {app.size} - {app.description}"
                    attr = curses.A_REVERSE if idx == current else curses.A_NORMAL
                    stdscr.addnstr(row, 0, line, width - 1, attr)

                footer = f"Selected: {len(selected)} of {len(apps)}"
                stdscr.addnstr(height - 1, 0, f"{footer} | {message}", width - 1)
                stdscr.refresh()

                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    current = max(0, current - 1)
                elif key in (curses.KEY_DOWN, ord("j")):
                    current = min(len(apps) - 1, current + 1)
                elif key == curses.KEY_NPAGE:
                    current = min(len(apps) - 1, current + visible_rows)
                elif key == curses.KEY_PPAGE:
                    current = max(0, current - visible_rows)
                elif key == ord(" "):
                    if current in selected:
                        selected.remove(current)
                    else:
                        selected.add(current)
                elif key in (ord("q"), 27):
                    return []
                elif key in (ord("d"), ord("D")):
                    if not selected:
                        message = "No applications are selected."
                        continue
                    names = ", ".join(apps[idx].display_name for idx in sorted(selected)[:4])
                    if len(selected) > 4:
                        names += ", ..."
                    question = f"Confirm deletion of {len(selected)} app(s): {names}? (y/n)"
                    stdscr.addnstr(height - 1, 0, question, width - 1)
                    stdscr.clrtoeol()
                    stdscr.refresh()
                    answer = stdscr.getch()
                    if answer in (ord("y"), ord("Y")):
                        return [apps[idx] for idx in sorted(selected)]
                    message = "Deletion canceled."

        return curses.wrapper(_run)

    def uninstall_packages_batch(self, packages: list[str]) -> bool:
        if not packages:
            warn("No applications were selected.")
            return False

        print()
        print(ctext(Color.CYAN, "Batch removal"))
        for package in packages:
            info(f"Removing '{package}'...")
            result = self.run_command(self.pkg_manager.remove_cmd + [package])
            if result.returncode == 0:
                success(f"Package '{package}' was removed successfully.")
            else:
                error(f"Failed to remove '{package}'.")
        return True

    def scan_config_apps(self) -> list[AppEntry]:
        info("Scanning .config directories for all users...")
        entries: list[AppEntry] = []
        seen: set[tuple[str, str]] = set()
        for config_dir in self.config_dirs():
            user = self.user_from_config_dir(config_dir)
            for item in sorted(config_dir.iterdir(), key=lambda p: p.name.lower()):
                if self.is_system_config(item.name) or self.is_generic_config_entry(item.name):
                    continue
                package = self.resolve_installed_package(item.name)
                if not package:
                    continue
                key = (user, package)
                if key in seen:
                    continue
                seen.add(key)
                description, size = self.describe_package(package)
                entries.append(
                    AppEntry(
                        package=package,
                        description=description,
                        size=size,
                        config_path=item,
                        user=user,
                    )
                )
        success(f"Applications found with .config entries: {len(entries)}")
        return entries

    def print_apps_table(self, apps: list[AppEntry]) -> None:
        print()
        print(ctext(Color.YELLOW, "Applications with configuration in .config"))
        print()
        print(f"{'#':<4} {'Name':<28} {'Size':<10} {'User':<10} Description")
        print("─" * 90)
        for idx, app in enumerate(apps, start=1):
            print(
                f"{idx:<4} {app.package:<28} {app.size:<10} {app.user:<10} {app.description[:42]}"
            )

    def search_package(self, search_term: str) -> list[tuple[str, str, str]]:
        matches: list[tuple[str, str, str]] = []
        for pkg in sorted(self.installed_packages):
            if search_term.lower() in pkg.lower():
                description, size = self.describe_package(pkg)
                matches.append((pkg, description, size))
        return matches

    def interactive_select_package(self) -> str | None:
        apps = self.scan_config_apps()
        while True:
            print()
            print(ctext(Color.MAGENTA, "Interactive mode"))
            print("  [1] List applications with entries in .config")
            print("  [2] Search for a package by name")
            print("  [3] Type the package name manually")
            print("  [0] Exit")
            choice = input("  Choose an option: ").strip()

            if choice == "1":
                if not apps:
                    warn("No installed applications with a .config folder were found.")
                    continue
                self.print_apps_table(apps)
                selected = input("\n  App number to remove (0 to go back): ").strip()
                if selected.isdigit():
                    index = int(selected)
                    if 1 <= index <= len(apps):
                        app = apps[index - 1]
                        success(f"Selected: {app.package}")
                        print(f"  Description: {app.description}")
                        print(f"  Size: {app.size}")
                        print(f"  Config: {app.config_path} (user: {app.user})")
                        return app.package
            elif choice == "2":
                term = input("  Search term: ").strip()
                if not term:
                    error("Empty value.")
                    continue
                matches = self.search_package(term)
                if not matches:
                    warn(f"No packages matching '{term}' were found.")
                    continue
                print()
                print(f"{'#':<4} {'Name':<28} {'Size':<10} Description")
                print("─" * 90)
                for idx, (pkg, desc, size) in enumerate(matches, start=1):
                    print(f"{idx:<4} {pkg:<28} {size:<10} {desc[:48]}")
                selected = input("\n  Package number to remove (0 to cancel): ").strip()
                if selected.isdigit():
                    index = int(selected)
                    if 1 <= index <= len(matches):
                        package = matches[index - 1][0]
                        success(f"Selected: {package}")
                        return package
            elif choice == "3":
                package = input("  Exact package name: ").strip()
                return package or None
            elif choice == "0":
                return None
            else:
                error("Invalid option.")

    def uninstall_package(self, package: str) -> bool:
        if not package:
            error("No package name was provided.")
            return False
        if not self.is_safe_package_name(package):
            error(f"Invalid or unsafe package name: '{package}'")
            return False

        info(f"Checking package: {package}...")
        installed = self.is_package_installed(package)
        if not installed:
            warn(f"The package '{package}' does not appear to be installed.")
            if not self.confirm("  Try removing it anyway?"):
                return False

        warn(f"Package scheduled for removal: {package}")
        if not self.confirm("  Continue with the removal?"):
            info("Removal canceled.")
            return False

        info(f"Removing '{package}'...")
        result = self.run_command(self.pkg_manager.remove_cmd + [package])
        if result.returncode == 0:
            success(f"Package '{package}' was removed successfully.")
            return True
        error(f"Failed to remove '{package}'.")
        return False

    def remove_orphans(self) -> None:
        print()
        info("Looking for orphaned dependencies...")
        if self.pkg_manager.name in {"apt", "dnf"}:
            result = self.run_command(self.pkg_manager.autoremove_cmd)
            if result.returncode == 0:
                success("Orphaned dependencies removed.")
            else:
                warn("No orphaned dependencies were found or an error occurred.")
            return

        result = self.run_command(["pacman", "-Qdtq"], capture=True)
        orphans = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not orphans:
            info("No orphaned packages were found.")
            return
        print(ctext(Color.YELLOW, "Orphaned packages found:"))
        for orphan in orphans:
            print(orphan)
        if self.confirm("  Remove these orphaned packages?"):
            cleanup = self.run_command(self.pkg_manager.autoremove_cmd + orphans)
            if cleanup.returncode == 0:
                success("Removed.")
            else:
                warn("Removal failed.")

    def clean_cache(self) -> None:
        print()
        if self.confirm("  Clean the downloaded package cache?"):
            result = self.run_command(self.pkg_manager.clean_cmd)
            if result.returncode == 0:
                success("Cache cleaned.")
            else:
                warn("Could not clean the cache.")

    def safe_remove_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path
        if resolved in PROTECTED_PATHS:
            return False
        if not str(resolved).strip():
            return False
        if resolved.is_file() or resolved.is_symlink():
            resolved.unlink(missing_ok=True)
        elif resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            return False
        return True

    def residual_patterns(self, package: str) -> list[str]:
        return [
            f"/etc/{package}",
            f"/var/log/{package}",
            f"/opt/{package}",
            f"/usr/local/{package}",
            f"/usr/share/{package}",
            f"/home/*/.config/{package}",
            f"/home/*/.{package}",
            f"/home/*/.local/share/{package}",
            f"/home/*/.cache/{package}",
            f"/root/.config/{package}",
            f"/root/.{package}",
            f"/home/*/.local/share/applications/{package}*",
            f"/usr/share/applications/{package}*",
            f"/etc/systemd/system/{package}*",
            f"/lib/systemd/system/{package}*",
        ]

    def expand_glob_pattern(self, pattern: str) -> Iterable[Path]:
        parent = Path(pattern).parent
        if "*" not in pattern and "?" not in pattern:
            target = Path(pattern)
            if target.exists():
                yield target
            return
        if not parent.exists():
            return
        name_pattern = Path(pattern).name
        yield from parent.glob(name_pattern)

    def find_residual_configs(self, package: str) -> None:
        print()
        info(f"Looking for leftover files for '{package}'...")
        if not self.is_safe_package_name(package):
            error(f"Invalid or unsafe package name: '{package}'")
            return

        found: list[Path] = []
        seen: set[Path] = set()
        for pattern in self.residual_patterns(package):
            for match in self.expand_glob_pattern(pattern):
                if match not in seen:
                    seen.add(match)
                    found.append(match)

        if not found:
            success("No leftover files were found.")
            return

        print(ctext(Color.YELLOW, "Leftover files found:"))
        for item in found:
            print(f"  - {item}")

        if self.confirm("  Remove these leftover files?"):
            removed = 0
            for item in found:
                if item.exists() and self.safe_remove_path(item):
                    removed += 1
                else:
                    warn(f"Skipping potentially dangerous path: {item}")
            success(f"Removed {removed} leftover files.")

    def path_size(self, path: Path) -> str:
        if path.is_symlink():
            return "(symlink)"
        try:
            result = self.run_command(["du", "-sh", str(path)], capture=True)
            return result.stdout.split()[0] if result.stdout.strip() else "0K"
        except Exception:
            return "0K"

    def scan_deep_orphans(self) -> list[OrphanEntry]:
        print()
        print(ctext(Color.CYAN, "Deep orphaned file scan"))
        info("Scanning .config directories for all users...")
        orphans: list[OrphanEntry] = []
        for config_dir in self.config_dirs():
            user = self.user_from_config_dir(config_dir)
            info(f"Scanning: {config_dir} (user: {user})")
            for item in sorted(config_dir.iterdir(), key=lambda p: p.name.lower()):
                if self.is_system_config(item.name) or self.is_generic_config_entry(item.name):
                    continue
                if self.is_package_installed(item.name):
                    continue
                entry_type = "symlink" if item.is_symlink() else "folder" if item.is_dir() else "file"
                orphan = OrphanEntry(
                    name=item.name,
                    path=item,
                    user=user,
                    size=self.path_size(item),
                    entry_type=entry_type,
                )
                orphans.append(orphan)
                warn(f"  [ORPHAN] {orphan.name} ({orphan.entry_type}, {orphan.size})")
            print()
        success(f"Scan complete: {len(orphans)} orphaned items found.")
        return orphans

    def show_orphan_results(self, orphans: list[OrphanEntry]) -> None:
        print()
        if not orphans:
            success("No orphaned files were found.")
            return
        print(ctext(Color.YELLOW, f"{len(orphans)} orphaned items were found:"))
        print(f"{'#':<4} {'Name':<30} {'Type':<10} {'Size':<10} {'User':<10} Path")
        print("─" * 110)
        for idx, orphan in enumerate(orphans, start=1):
            path_str = str(orphan.path)
            if len(path_str) > 45:
                path_str = f"...{path_str[-42:]}"
            print(
                f"{idx:<4} {orphan.name:<30} {orphan.entry_type:<10} {orphan.size:<10} "
                f"{orphan.user:<10} {path_str}"
            )

    def export_orphans(self, orphans: list[OrphanEntry]) -> None:
        default_name = f"/tmp/config_huerfanos_{datetime.now():%Y%m%d_%H%M%S}.txt"
        output_file = input(f"  Output file name [{default_name}]: ").strip() or default_name
        with open(output_file, "w", encoding="utf-8") as fh:
            fh.write("desinstalar.py - Orphaned files in .config\n")
            fh.write(f"Generated: {datetime.now()}\n")
            fh.write(f"Total: {len(orphans)}\n\n")
            for orphan in orphans:
                fh.write(
                    f"{orphan.name} | {orphan.entry_type} | {orphan.size} | "
                    f"{orphan.user} | {orphan.path}\n"
                )
        success(f"Exported to: {output_file}")

    def delete_orphans_by_indices(self, orphans: list[OrphanEntry], indices: list[int]) -> list[OrphanEntry]:
        deleted = 0
        remaining = list(orphans)
        for index in sorted(set(indices), reverse=True):
            if 0 <= index < len(remaining):
                target = remaining[index]
                if target.path.exists() and self.safe_remove_path(target.path):
                    deleted += 1
                    remaining.pop(index)
        success(f"Removed {deleted} items.")
        return remaining

    def orphan_action_menu(self, orphans: list[OrphanEntry]) -> None:
        while orphans:
            print()
            print(ctext(Color.MAGENTA, "What would you like to do?"))
            print("  [1] Remove ONE orphaned item")
            print("  [2] Remove MULTIPLE items (example: 1,3,5-7)")
            print("  [3] Remove ALL orphaned items")
            print("  [4] Export the list to a file")
            print("  [0] Exit without making changes")
            choice = input("  Option: ").strip()
            if choice == "1":
                selected = input("  Item number to remove: ").strip()
                if selected.isdigit():
                    index = int(selected) - 1
                    orphans = self.delete_orphans_by_indices(orphans, [index])
            elif choice == "2":
                raw = input("  Items to remove: ").strip()
                indices: list[int] = []
                for part in raw.split(","):
                    part = part.strip()
                    if re.fullmatch(r"\d+", part):
                        indices.append(int(part) - 1)
                    elif re.fullmatch(r"\d+-\d+", part):
                        start, end = (int(v) for v in part.split("-", 1))
                        indices.extend(range(start - 1, end))
                orphans = self.delete_orphans_by_indices(orphans, indices)
            elif choice == "3":
                if self.confirm("Type 'y' to CONFIRM removal of ALL items"):
                    orphans = self.delete_orphans_by_indices(orphans, list(range(len(orphans))))
            elif choice == "4":
                self.export_orphans(orphans)
            elif choice == "0":
                info("Exiting without changes.")
                return
            else:
                error("Invalid option.")
            self.show_orphan_results(orphans)

    def find_orphan_files(self) -> None:
        orphans = self.scan_deep_orphans()
        self.show_orphan_results(orphans)
        self.orphan_action_menu(orphans)

    def extra_cleanup(self) -> None:
        print()
        print(ctext(Color.CYAN, "Extra cleanup"))
        if self.confirm("  Remove temporary script files in /tmp (cleanup.*, desinstalar.*)?"):
            for pattern in ("cleanup.*", "desinstalar.*"):
                for item in Path("/tmp").glob(pattern):
                    if item.exists():
                        self.safe_remove_path(item)
            success("Script temporary files cleaned.")

        if shutil.which("journalctl") and self.confirm("  Vacuum logs older than 7 days?"):
            result = self.run_command(["journalctl", "--vacuum-time=7d"])
            if result.returncode == 0:
                success("Logs vacuumed.")

        if self.confirm("  Empty trash folders?"):
            for trash in Path("/home").glob("*/.local/share/Trash"):
                if trash.is_dir():
                    for item in trash.iterdir():
                        self.safe_remove_path(item)
            root_trash = Path("/root/.local/share/Trash")
            if root_trash.is_dir():
                for item in root_trash.iterdir():
                    self.safe_remove_path(item)
            success("Trash folders emptied.")

    def show_summary(self, package: str) -> None:
        print()
        print(ctext(Color.CYAN, "Final summary"))
        print(f"  Package: {ctext(Color.GREEN, package)}")
        print(f"  Manager: {ctext(Color.BLUE, self.pkg_manager.name)}")
        print()
        print(ctext(Color.GREEN, "Cleanup completed."))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desinstalar.py",
        description=(
            "Tool for removing applications and cleaning leftover "
            "dependencies, cache, and residual configuration files."
        ),
    )
    parser.add_argument("package", nargs="?", help="Package to remove")
    parser.add_argument("-o", "--orphans", action="store_true", help="Search for orphaned files")
    parser.add_argument(
        "-all",
        "--all",
        action="store_true",
        dest="all_mode",
        help="Automatic mode with no prompts (requires a package)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Auto-confirm prompts (requires a package)",
    )
    parser.add_argument(
        "-p",
        "--pick-apps",
        action="store_true",
        help="List user-installed applications and allow multi-select removal with arrow keys",
    )
    return parser


def validate_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must be run as root (use 'sudo').")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    auto_yes = args.all_mode or args.yes
    validate_root()
    app = CleanUninstallApp(auto_yes=auto_yes)
    app.print_banner()

    if args.orphans:
        app.find_orphan_files()
        return 0

    if args.pick_apps:
        apps = app.list_user_installed_apps()
        if not apps:
            warn("No user-installed applications were found to display.")
            return 0
        selected = app.package_picker_ui(apps)
        if not selected:
            info("No changes made.")
            return 0
        if not app.uninstall_packages_batch([item.package for item in selected]):
            return 1
        app.remove_orphans()
        app.clean_cache()
        app.show_summary(", ".join(item.package for item in selected))
        return 0

    package = args.package
    if not package:
        if auto_yes:
            error("The -all mode requires a package name. Example: sudo ./desinstalar.py -all firefox")
            return 1
        warn("No package was specified.")
        package = app.interactive_select_package()
        if not package:
            return 1

    print()
    print(ctext(Color.CYAN, f"Processing package: {package}"))
    if not app.uninstall_package(package):
        warn("Removal was not completed.")
        return 1

    app.remove_orphans()
    app.clean_cache()
    app.find_residual_configs(package)
    app.extra_cleanup()
    app.show_summary(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
