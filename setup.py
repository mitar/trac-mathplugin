from setuptools import setup

VERSION = '0.5'
PACKAGE = 'tracmath'

setup(
    name = 'TracMath',
    version = VERSION,
    maintainer = "Kamil Kisiel",
    maintainer_email = "kamil@kamilkisiel.net",
    packages = [PACKAGE],
    include_package_data = True,
    package_data = {
        PACKAGE: ['templates/*.tex'],
    },
    zip_safe = False,
    entry_points = {
        'trac.plugins': '%s = %s' % (PACKAGE, PACKAGE),
    },
)
