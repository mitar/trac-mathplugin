from setuptools import find_packages, setup

setup(
    name='TracMath', version='0.3',
    maintainer="Kamil Kisiel",
    maintainer_email="kamil@kamilkisiel.net",
    packages=find_packages(exclude=['*.tests*']),
    entry_points="""
    [trac.plugins]
    tracmath = tracmath
    """,
)
