# Package provenance

The pinned base is the official Python Debian Bookworm image. Packages are
installed from Debian main; their exact installed versions are recorded inside
the resulting image at /usr/share/open-dots-packages.txt. Debian security updates
mean local builds on different dates can have different package versions.

Each package's copyright and licence texts remain under /usr/share/doc.
Corresponding Debian source packages can be retrieved with apt source using
matching deb-src repositories, or from https://snapshot.debian.org/ and
https://sources.debian.org/. TigerVNC, Openbox, maim, socat and ImageMagick include
copyleft components; Firefox ESR includes MPL-licensed components. Review all
package licences and corresponding source obligations before redistributing a
built image. This repository supplies a local build recipe and publishes no image.

Chromium comes from Debian's security repository. The Python Playwright client
is pinned to 1.58.0 from PyPI (Apache-2.0); its bundled driver and licence notices
remain in site-packages/playwright. No Playwright browser download is used;
the client attaches over CDP to Debian's installed Chromium.
