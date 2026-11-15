#!/usr/bin/env python3
"""Download and build the Windows x64 WhisperDesktop examples."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path, PureWindowsPath


REPOSITORY_ZIP = (
    "https://github.com/sffxzzp/WhisperDesktop/archive/refs/heads/master.zip"
)
BUILD_TOOLS_PAGE = "https://visualstudio.microsoft.com/visual-cpp-build-tools/"
BUILD_TOOLS_INSTALLER = "https://aka.ms/vs/17/release/vs_BuildTools.exe"
DOTNET_PAGE = "https://dotnet.microsoft.com/download/dotnet/10.0"
VSWHERE_RELATIVE_PATH = Path("Microsoft Visual Studio", "Installer", "vswhere.exe")
VS_SETUP_RELATIVE_PATH = Path("Microsoft Visual Studio", "Installer", "setup.exe")
MSVC_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
WINDOWS_SDK_COMPONENT = "Microsoft.VisualStudio.Component.Windows11SDK.26100"


def find_vswhere():
    program_files = os.environ.get("ProgramFiles(x86)")
    if not program_files:
        program_files = os.environ.get("ProgramFiles")
    if not program_files:
        return None

    vswhere = Path(program_files) / VSWHERE_RELATIVE_PATH
    return vswhere if vswhere.is_file() else None


def find_visual_studio_instances():
    vswhere = find_vswhere()
    if vswhere is None:
        return []
    result = subprocess.run(
        [
            str(vswhere),
            "-all",
            "-products",
            "*",
            "-property",
            "installationPath",
            "-utf8",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    return [
        path
        for line in result.stdout.splitlines()
        if (path := Path(line.strip())).is_dir()
    ]


def has_msbuild(installation):
    return (
        installation / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
    ).is_file()


def find_visual_studio_instance():
    installations = find_visual_studio_instances()
    return next(
        (
            installation
            for installation in installations
            if has_msbuild(installation)
            and has_visual_studio_2022(installation)
        ),
        next(
            (
                installation
                for installation in installations
                if has_visual_studio_2022(installation)
            ),
            None,
        ),
    )


def find_msbuild():
    installations = find_visual_studio_instances()
    for installation in installations:
        candidate = installation / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
        if candidate.is_file() and has_v143_tools(installation):
            return candidate
    return None


def find_visual_studio_instance_info(installation):
    vswhere = find_vswhere()
    if vswhere is None:
        return None

    result = subprocess.run(
        [str(vswhere), "-all", "-products", "*", "-format", "json", "-utf8"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0 or not result.stdout:
        return None

    try:
        instances = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("vswhere вернул некорректный JSON.") from error

    for instance in instances:
        instance_path = instance.get("installationPath")
        if (
            instance_path
            and Path(instance_path).resolve() == installation.resolve()
        ):
            return instance
    return None


def find_visual_studio_channel_id(installation):
    instance = find_visual_studio_instance_info(installation)
    return instance.get("channelId") if instance else None


def has_visual_studio_2022(installation):
    instance = find_visual_studio_instance_info(installation)
    version = instance.get("installationVersion") if instance else None
    if not isinstance(version, str):
        return False
    try:
        return int(version.split(".", maxsplit=1)[0]) == 17
    except ValueError:
        return False


def has_v143_tools(installation):
    if not has_visual_studio_2022(installation):
        return False
    compiler_pattern = "*/bin/Hostx64/x64/cl.exe"
    return any((installation / "VC" / "Tools" / "MSVC").glob(compiler_pattern))


def missing_dependencies():
    missing = []

    installations = find_visual_studio_instances()
    ready_instance = next(
        (
            installation
            for installation in installations
            if has_msbuild(installation) and has_v143_tools(installation)
        ),
        None,
    )
    if ready_instance is None:
        installation = find_visual_studio_instance()
        components = [
            "--add",
            "Microsoft.VisualStudio.Workload.VCTools",
            "--add",
            MSVC_COMPONENT,
            "--add",
            WINDOWS_SDK_COMPONENT,
        ]
        if installation:
            installer_args = None
            installer_url = None
        else:
            installer_args = [
                "--wait",
                "--passive",
                "--norestart",
                *components,
            ]
            installer_url = BUILD_TOOLS_INSTALLER
        missing.append(
            (
                "MSVC v143, MSBuild и Windows SDK (Visual Studio 2022)",
                None,
                BUILD_TOOLS_PAGE,
                installer_args,
                installer_url,
                installation,
            )
        )

    dotnet = shutil.which("dotnet")
    if dotnet is None:
        missing.append(
            (
                ".NET 10 SDK",
                "Microsoft.DotNet.SDK.10",
                DOTNET_PAGE,
                None,
                None,
                None,
            )
        )
    else:
        result = subprocess.run(
            [dotnet, "--list-sdks"], check=False, capture_output=True, text=True
        )
        if result.returncode != 0 or not any(
            line.split(maxsplit=1)[0].startswith("10.")
            for line in result.stdout.splitlines()
            if line.strip()
        ):
            missing.append(
                (
                    ".NET 10 SDK",
                    "Microsoft.DotNet.SDK.10",
                    DOTNET_PAGE,
                    None,
                    None,
                    None,
                )
            )

    return missing


def install_missing_dependencies():
    missing = missing_dependencies()
    if not missing:
        return True

    winget = shutil.which("winget")
    for (
        name,
        package_id,
        download_page,
        installer_args,
        installer_url,
        installation,
    ) in missing:
        print(f"\nНе найдено: {name}.")
        if installation:
            answer = input(
                f"Проект требует MSVC v143. "
                f"1 — добавить нужные компоненты в {installation}, "
                "2 — открыть страницу Visual Studio, 3 — пропустить и завершить: "
            ).strip()
        elif installer_url:
            answer = input(
                "Visual Studio 2022 с компонентами C++ не найдена. "
                "1 — скачать и запустить официальный установщик Visual Studio 2022 Build Tools, "
                "2 — открыть страницу загрузки, 3 — пропустить и завершить: "
            ).strip()
        elif winget:
            answer = input(
                "1 — установить через winget, 2 — открыть страницу загрузки, "
                "3 — пропустить и завершить: "
            ).strip()
        else:
            answer = input(
                "winget не найден. 2 — открыть страницу загрузки или "
                "3 — пропустить и завершить: "
            ).strip()

        if answer == "1" and installation:
            vswhere = find_vswhere()
            if vswhere is None:
                raise FileNotFoundError("Не найден vswhere.exe установленной Visual Studio.")
            channel_id = find_visual_studio_channel_id(installation)
            if not channel_id:
                raise RuntimeError(
                    f"Не удалось определить channelId для Visual Studio: {installation}"
                )
            setup = vswhere.with_name("setup.exe")
            if not setup.is_file():
                raise FileNotFoundError(f"Не найден установщик Visual Studio: {setup}")
            command = [
                str(setup),
                "modify",
                "--installPath",
                str(installation),
                "--channelId",
                channel_id,
                "--add",
                "Microsoft.VisualStudio.Workload.VCTools",
                "--add",
                MSVC_COMPONENT,
                "--add",
                WINDOWS_SDK_COMPONENT,
                "--passive",
                "--norestart",
            ]
            result = subprocess.run(command, check=False)
            if result.returncode not in (0, 1641, 3010):
                raise subprocess.CalledProcessError(result.returncode, command)
        elif answer == "1" and installer_url:
            with tempfile.TemporaryDirectory(
                prefix="visual-studio-build-tools-"
            ) as temporary:
                installer = Path(temporary) / "vs_BuildTools.exe"
                print(f"Скачиваю официальный установщик: {installer_url}")
                with urllib.request.urlopen(installer_url, timeout=60) as response:
                    with installer.open("wb") as output:
                        shutil.copyfileobj(response, output)

                result = subprocess.run(
                    [str(installer), *installer_args], check=False
                )
                if result.returncode not in (0, 1641, 3010):
                    raise subprocess.CalledProcessError(
                        result.returncode, [str(installer), *installer_args]
                    )
        elif answer == "1" and winget and package_id:
            command = [
                winget,
                "install",
                "--exact",
                "--id",
                package_id,
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
            subprocess.run(command, check=True)
        elif answer == "2":
            webbrowser.open(download_page)
            print("Установите компонент и запустите скрипт ещё раз.")
            return False
        else:
            print("Установка пропущена; сборка отменена.")
            return False

    remaining = missing_dependencies()
    if remaining:
        print("\nНе удалось обнаружить необходимые компоненты после установки:")
        for name, *_ in remaining:
            print(f"  - {name}")
        print("Перезапустите терминал или компьютер, затем запустите скрипт ещё раз.")
        return False
    return True


def safe_extract(archive, destination):
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            windows_path = PureWindowsPath(entry.filename)
            if (
                windows_path.is_absolute()
                or windows_path.drive
                or ".." in windows_path.parts
            ):
                raise ValueError(f"Недопустимый путь в архиве: {entry.filename}")

            target = (destination / Path(*windows_path.parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Недопустимый путь в архиве: {entry.filename}")
        bundle.extractall(destination)


def download_source(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not (destination / "WhisperCpp.sln").is_file():
            raise FileExistsError(
                f"Папка уже существует и не содержит WhisperCpp.sln: {destination}\n"
                "Укажите другую папку через параметр --output."
            )
        answer = input(
            f"Папка {destination} уже содержит исходники. "
            "[И] использовать их или [О] отменить? "
        ).strip().lower()
        if answer in ("и", "i", "y", "yes"):
            return
        raise RuntimeError(
            "Скачивание отменено. Укажите новую папку через параметр --output."
        )

    with tempfile.TemporaryDirectory(
        prefix="whisperdesktop-download-", dir=destination.parent
    ) as temporary:
        temporary = Path(temporary)
        archive = temporary / "source.zip"
        print(f"Скачиваю исходники: {REPOSITORY_ZIP}")
        with urllib.request.urlopen(REPOSITORY_ZIP, timeout=60) as response:
            with archive.open("wb") as output:
                shutil.copyfileobj(response, output)

        extracted = temporary / "extracted"
        extracted.mkdir()
        safe_extract(archive, extracted)

        roots = [
            path
            for path in extracted.iterdir()
            if path.is_dir() and (path / "WhisperCpp.sln").is_file()
        ]
        if len(roots) != 1:
            raise ValueError("В архиве не найдена ожидаемая структура проекта.")
        shutil.move(str(roots[0]), str(destination))


def run_command(command, cwd):
    print("\n> " + subprocess.list2cmdline([str(part) for part in command]))
    subprocess.run(command, cwd=cwd, check=True)


def find_binary(repository, name):
    matches = [
        path
        for path in repository.rglob(name)
        if "artifacts" not in path.relative_to(repository).parts
    ]
    if not matches:
        raise FileNotFoundError(f"После сборки не найден файл {name}.")
    return matches[0]


def collect_artifacts(repository):
    artifacts = repository / "artifacts"
    desktop = artifacts / "WhisperDesktop"
    cli = artifacts / "WhisperCLI"
    desktop.mkdir(parents=True, exist_ok=True)
    cli.mkdir(parents=True, exist_ok=True)

    files = (
        (find_binary(repository, "WhisperDesktop.exe"), desktop),
        (repository / "Examples" / "WhisperDesktop" / "Readme.txt", desktop),
        (repository / "Examples" / "main" / "Readme.txt", cli),
    )
    whisper_dll = find_binary(repository, "Whisper.dll")
    files += ((whisper_dll, desktop), (whisper_dll, cli))

    for source, target in files:
        if not source.is_file():
            raise FileNotFoundError(f"Не найден обязательный файл: {source}")
        shutil.copy2(source, target / source.name)

    cli_executable = find_binary(repository, "main.exe")
    if not cli_executable.is_file():
        raise FileNotFoundError(f"Не найден обязательный файл: {cli_executable}")
    shutil.copy2(cli_executable, cli / "WhisperCLI.exe")

    for name, folder in (("WhisperDesktop", desktop), ("WhisperCLI", cli)):
        archive = artifacts / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in folder.iterdir():
                bundle.write(path, path.name)

    print("\nСборка завершена. Архивы:")
    print(f"  {artifacts / 'WhisperDesktop.zip'}")
    print(f"  {artifacts / 'WhisperCLI.zip'}")


def build(repository):
    msbuild = find_msbuild()
    dotnet = shutil.which("dotnet")
    if msbuild is None or dotnet is None:
        raise RuntimeError("Инструменты сборки не найдены после проверки.")

    run_command(
        [
            str(msbuild),
            "ComputeShaders\\ComputeShaders.vcxproj",
            "/p:Configuration=Release",
            "/p:Platform=x64",
            "/m",
            "/v:m",
        ],
        repository,
    )
    run_command(
        [
            dotnet,
            "run",
            "--project",
            "Tools/CompressShaders/CompressShaders.csproj",
            "-c",
            "Release",
        ],
        repository,
    )
    run_command(
        [
            str(msbuild),
            "WhisperCpp.sln",
            "/p:Configuration=Release",
            "/p:Platform=x64",
            "/m",
            "/v:m",
            "/t:Examples\\WhisperDesktop;Examples\\main",
            "/p:BuildProjectReferences=true",
        ],
        repository,
    )
    collect_artifacts(repository)


def main():
    parser = argparse.ArgumentParser(
        description="Скачать WhisperDesktop и собрать Windows-примеры x64."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "WhisperDesktop-build",
        help=(
            "папка для скачанного исходного кода "
            "(по умолчанию: ./WhisperDesktop-build)"
        ),
    )
    args = parser.parse_args()

    if os.name != "nt":
        print("Этот проект собирается только в Windows; запустите скрипт там.")
        return 1
    if sys.version_info < (3, 9):
        print("Требуется Python 3.9 или новее.")
        return 1
    if not install_missing_dependencies():
        return 1

    repository = args.output.resolve()
    download_source(repository)
    build(repository)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"\nОшибка: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nОперация отменена.", file=sys.stderr)
        raise SystemExit(130)
