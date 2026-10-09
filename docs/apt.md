# Ubuntu APT package

Salt TUI ships native Debian packaging for Ubuntu 24.04 LTS (Noble) and newer. It requires Python 3.11 or newer. Salt CLI executables remain optional runtime integrations; installing this package does not install or configure a Salt master or minion.

## Maintainer setup

Create a Launchpad PPA named `salt-tui` under the `pratik-anurag` account (or update the commands below for an organisation-owned PPA). Publish the source package from a trusted workstation with the Launchpad-authorised signing key:

```sh
git checkout vX.Y.Z
sudo apt install devscripts debhelper dh-sequence-python3 pybuild-plugin-pyproject python3-all python3-hatchling
dpkg-buildpackage -S -sa
dput ppa:pratik-anurag/salt-tui ../salt-tui_*.changes
```

Launchpad builds the binary package for Noble. Wait for the build to finish successfully before documenting the repository as available.

## User installation

After the PPA has published a successful build, Ubuntu users install it with:

```sh
sudo add-apt-repository ppa:pratik-anurag/salt-tui
sudo apt update
sudo apt install salt-tui
```

Confirm the package and discover available Salt integrations with:

```sh
salt-tui --version
salt-tui --help
```

## Release checks

The CI workflow builds a `.deb` on Ubuntu and validates its metadata on every change. Before a PPA upload, run the standard Python release checks plus `dpkg-buildpackage -S -sa` on an Ubuntu Noble environment. The package version in `debian/changelog` must match the Python package version with a `-1` Debian revision.
