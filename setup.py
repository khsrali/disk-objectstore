"""Install the disk-objectstore implementation."""
import os
import io

try:
    from setuptools import setup, find_packages
except ImportError:
    from distutils.core import setup

MODULENAME = 'disk_objectstore'
THE_LICENSE = 'The MIT license'

# Get the version number in a dirty way
FOLDER = os.path.split(os.path.abspath(__file__))[0]
FNAME = os.path.join(FOLDER, MODULENAME, '__init__.py')
with open(FNAME) as init:
    # Get lines that match, remove comment part
    # (assuming it's not in the string...)
    VERSIONLINES = [l.partition('#')[0] for l in init.readlines() if l.startswith('__version__')]
assert len(VERSIONLINES) == 1, 'Unable to detect the version lines'
VERSIONLINE = VERSIONLINES[0]
VERSION = VERSIONLINE.partition('=')[2].replace('"', '').strip()

setup(
    name=MODULENAME,
    description='An implementation of an efficient object store writing directly into a disk folder',
    url='http://github.com/giovannipizzi/disk-objectstore',
    license=THE_LICENSE,
    author='Giovanni Pizzi',
    version=VERSION,
    install_requires=[
        'sqlalchemy',
    ],
    extras_require={
        'testing': [
            'profilehooks',
            'psutil',
            'click',
            'numpy',
        ],
        'dev': [
            'pre-commit',
            'yapf==0.29.0',
            'prospector==0.12.11',
        ],
    },
    packages=find_packages(),
    # Needed to include some static files declared in MANIFEST.in
    include_package_data=True,
    keywords=[
        'object store',
        'repository',
        'file store',
    ],
    long_description=io.open(os.path.join(FOLDER, 'README.md'), encoding='utf-8').read(),
    long_description_content_type='text/markdown',
    classifiers=[
        'Programming Language :: Python :: 3', 'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent', 'Topic :: Software Development :: Libraries :: Python Modules'
    ],
)