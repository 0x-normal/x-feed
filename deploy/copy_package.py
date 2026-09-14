"""Copy runtime Python files and all declared package assets into an installation."""
import argparse
from pathlib import Path
import shutil
import tomllib


def runtime_files(source):
    source = Path(source)
    package = source / 'x_engine'
    manifest = tomllib.loads((source / 'pyproject.toml').read_text(encoding='utf-8'))
    files = set(package.glob('*.py'))
    if not (package / 'cli.py').is_file():
        raise ValueError('Source checkout is missing x_engine/cli.py.')
    for pattern in manifest['tool']['setuptools']['package-data']['x_engine']:
        matches = [path for path in package.glob(pattern) if path.is_file()]
        if not matches:
            raise ValueError(f'Source checkout is missing package asset: {pattern}')
        files.update(matches)
    return sorted(files)


def copy_package(source, destination):
    source, destination = Path(source), Path(destination)
    files = runtime_files(source)
    for path in files:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        runtime_files(args.source)
    else:
        copy_package(args.source, args.destination)
