# cleanUninstall

`cleanUninstall` is a terminal tool for Linux that removes applications and helps clean leftover files, orphaned dependencies, cache, and residual configuration.

It currently supports:

- `apt` on Debian, Ubuntu, and Linux Mint
- `dnf` on Fedora
- `pacman` on Arch Linux

## Features

- Remove a package directly by name
- Interactive package selection
- Deep scan for orphaned files in `.config`
- Multi-select app picker with keyboard navigation
- Cache cleanup and optional extra cleanup tasks
- Safer path filtering to avoid deleting critical system directories

## Requirements

- Linux
- Python 3.12+
- Root privileges with `sudo`

## Usage

Show help:

```bash
sudo python3 desinstalar.py --help
```

Remove a package directly:

```bash
sudo python3 desinstalar.py firefox
```

Automatic mode:

```bash
sudo python3 desinstalar.py -all firefox
```

Find orphaned files:

```bash
sudo python3 desinstalar.py -o
```

Open the user-installed app picker:

```bash
sudo python3 desinstalar.py -p
```

## Keyboard Shortcuts In `-p`

- Arrow keys: move through the list
- `Space`: select or unselect an app
- `[d]`: delete selected apps
- `[q]`: exit without changes

## Project Status

This project started as a Bash script and is being migrated to Python for better structure and maintainability.

## License

This project is licensed under the GNU General Public License v3.0.
