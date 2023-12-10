#
# Copyright (c) 2023 TUM Department of Electrical and Computer Engineering.
#
# This file is part of Seal5.
# See https://github.com/tum-ei-eda/seal5.git for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Seal5 Flow API."""
import re
import os
import tarfile
from enum import Enum, IntEnum, auto
from pathlib import Path
from typing import Optional, List

import git
import yaml

from seal5.logging import get_logger, set_log_file, set_log_level
from seal5.dependencies import m2isar_dependency, cdsl2llvm_dependency
from seal5 import utils

logger = get_logger()


def clean_path(path: Path, interactive: bool = False):
    raise NotImplementedError


def merge_dicts(a: dict, b: dict, path=[]):
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_dicts(a[key], b[key], path + [str(key)])
            elif a[key] != b[key]:
                # raise Exception("Conflict at " + ".".join(path + [str(key)]))
                assert type(a[key]) is type(b[key])
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def get_cmake_args(cfg: dict):
    ret = []
    for key, value in cfg.items():
        if isinstance(value, bool):
            value = "ON" if value else "OFF"
        elif isinstance(value, list):
            value = ";".join(value)
        else:
            assert isinstance(value, (int, str)), "Unsupported cmake cfg"
        ret.append(f"-D{key}={value}")
    return ret


def build_llvm(
    src: Path, dest: Path, debug: bool = False, use_ninja: bool = False, verbose: bool = False, cmake_options: dict = {}
):
    cmake_args = get_cmake_args(cmake_options)
    dest.mkdir(exist_ok=True)
    utils.cmake(
        src / "llvm",
        *cmake_args,
        use_ninja=use_ninja,
        cwd=dest,
        print_func=logger.info if verbose else logger.debug,
        live=True,
    )
    utils.make(cwd=dest, print_func=logger.info if verbose else logger.debug, live=True)


def test_llvm(base: Path, build_dir: Path, test_paths: List[str] = [], verbose: bool = False):
    lit_exe = build_dir / "bin" / "llvm-lit"
    failing_tests = []
    for test_path in test_paths:

        def handler(code):
            return 0

        out = utils.exec_getout(
            lit_exe,
            base / test_path,
            print_func=logger.info if verbose else logger.debug,
            live=True,
            handle_exit=handler,
        )
        failing = re.compile(r"FAIL: LLVM :: (.*) \(").findall(out)
        if len(failing) > 0:
            failing_tests.extend(failing)

    return failing_tests


class Seal5State(Enum):
    UNKNOWN = auto()
    UNINITIALIZED = auto()
    INITIALIZED = auto()

class PatchStage(IntEnum):
    PHASE_0 = 0
    PHASE_1 = 1
    PHASE_2 = 2
    PHASE_3 = 3
    PHASE_4 = 4


DEFAULT_SETTINGS = {
    # "directory": ?,
    "logging": {
        "console": {
            "level": "INFO",
        },
        "file": {
            "level": "DEBUG",
            "rotate": False,
            "limit": 1000,
        },
    },
    "patch": {
        "author": "Seal5",
        "mail": "example@example.com",
    },
    "filter": {
        "sets": {
            "keep": [],
            "drop": [],
        },
        "instructions": {
            "keep": [],
            "drop": [],
        },
        "aliases": {
            "keep": [],
            "drop": [],
        },
        "intrinsics": {
            "keep": [],
            "drop": [],
        },
    },
    "transform": {
        "passes": "*",
    },
    "test": {
        "paths": ["MC/RISCV", "CodeGen/RISCV"],
    },
    "llvm": {
        "state": {"version": "auto", "base_commit": "unknown"},
        "configs": {
            "release": {
                "options": {
                    "CMAKE_BUILD_TYPE": "Release",
                    "LLVM_BUILD_TOOLS": True,
                    "LLVM_ENABLE_ASSERTIONS": False,
                    "LLVM_OPTIMIZED_TABLEGEN": True,
                    "LLVM_ENABLE_PROJECTS": ["clang", "lld"],
                    "LLVM_TARGETS_TO_BUILD": ["X86", "RISCV"],
                },
            },
            "release_assertions": {
                "options": {
                    "CMAKE_BUILD_TYPE": "Release",
                    "LLVM_BUILD_TOOLS": True,
                    "LLVM_ENABLE_ASSERTIONS": True,
                    "LLVM_OPTIMIZED_TABLEGEN": True,
                    "LLVM_ENABLE_PROJECTS": ["clang", "lld"],
                    "LLVM_TARGETS_TO_BUILD": ["X86", "RISCV"],
                },
            },
            "debug": {
                "options": {
                    "CMAKE_BUILD_TYPE": "Debug",
                    "LLVM_BUILD_TOOLS": True,
                    "LLVM_ENABLE_ASSERTIONS": True,
                    "LLVM_OPTIMIZED_TABLEGEN": True,
                    "LLVM_ENABLE_PROJECTS": ["clang", "lld"],
                    "LLVM_TARGETS_TO_BUILD": ["X86", "RISCV"],
                },
            },
        },
    },
    "inputs": [],
    "extensions": {
        # RV32Zpsfoperand:
        #   feature: RV32Zpsfoperand
        #   arch: rv32zpsfoperand
        #   version: "1.0"
        #   experimental: true
        #   vendor: false
        #   instructions/intrinsics/aliases/constraints: TODO
        #   # patches: []
    },
    "groups": {
        "all": "*",
    },
}


class YAMLSettings:
    @staticmethod
    def from_yaml(text: str):
        data = yaml.safe_load(text)
        return Seal5Settings(data=data)

    @staticmethod
    def from_yaml_file(path: Path):
        with open(path, "r") as file:
            data = yaml.safe_load(file)
        return Seal5Settings(data=data)

    def __init__(self, data: dict = {}):
        self.data: dict = data
        assert self.validate()

    def to_yaml(self):
        data = self.data
        text = yaml.dump(data)
        return text

    def to_yaml_file(self, path: Path):
        text = self.to_yaml()
        with open(path, "w") as file:
            file.write(text)

    def validate(self):
        # TODO
        return True

    def merge(self, other: "YAMLSettings", overwrite: bool = False):
        # TODO:
        if overwrite:
            self.data.update(other.data)
        else:
            self.data = merge_dicts(self.data, other.data)


class TestSettings(YAMLSettings):
    @property
    def paths(self):
        return self.data["paths"]


class LoggingSettings(YAMLSettings):
    @property
    def console(self):
        return self.data["console"]

    @property
    def file(self):
        return self.data["file"]


class FilterSettings(YAMLSettings):
    @property
    def sets(self):
        return self.data.get("sets")

    @property
    def instructions(self):
        return self.data.get("instructions")

    @property
    def aliases(self):
        return self.data.get("aliases")

    @property
    def intrinsics(self):
        return self.data.get("intrinsics")


class LLVMSettings(YAMLSettings):
    @property
    def state(self):
        return self.data["state"]

    @property
    def configs(self):
        return self.data["configs"]


class Seal5Settings(YAMLSettings):
    @property
    def logging(self):
        return LoggingSettings(data=self.data["logging"])

    @property
    def filter(self):
        return FilterSettings(data=self.data["filter"])

    @property
    def llvm(self):
        return LLVMSettings(data=self.data["llvm"])

    @property
    def patch(self):
        return PatchSettings(data=self.data["patch"])

    @property
    def transform(self):
        return TransformSettings(data=self.data["transform"])

    @property
    def test(self):
        return TestSettings(data=self.data["test"])

    @property
    def extensions(self):
        return ExtensionsSettings(data=self.data["extensions"])

    @property
    def groups(self):
        return GroupsSettings(data=self.data["groups"])


def handle_directory(directory: Optional[Path]):
    # TODO: handle environment vars
    if directory is None:
        assert NotImplementedError
    if not isinstance(directory, Path):
        path = Path(directory)
    return path


def create_seal5_directories(path: Path, directories: list):
    logger.debug("Creating Seal5 directories")
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_dir():
        raise RuntimeError(f"Not a diretory: {path}")
    for directory in directories:
        (path / directory).mkdir(parents=True, exist_ok=True)


def clone_llvm_repo(dest: Path, clone_url: str, ref: Optional[str] = None):  # TODO: how to get submodule url/ref
    logger.debug("Cloning LLVM repository: %s", clone_url)
    repo = git.Repo.clone_from(clone_url, dest, no_checkout=ref is not None)
    if ref:
        logger.debug("Checking out branch: %s", ref)
        repo.git.checkout(ref)


class Seal5Flow:
    def __init__(self, directory: Optional[Path] = None, name: str = "default"):
        self.directory: Path = handle_directory(directory)
        self.name: str = name
        self.state: Seal5State = Seal5State.UNKNOWN
        self.check()
        self.settings: Seal5Settings = None
        if self.settings_file.is_file():
            self.settings = Seal5Settings.from_yaml_file(self.settings_file)
        if self.logs_dir.is_dir():
            set_log_file(self.log_file_path)
            if self.settings:
                set_log_level(
                    console_level=self.settings.logging.console["level"], file_level=self.settings.logging.file["level"]
                )

    @property
    def meta_dir(self):
        return self.directory / ".seal5"

    @property
    def settings_file(self):
        return self.meta_dir / "settings.yml"

    @property
    def deps_dir(self):
        return self.meta_dir / "deps"

    @property
    def build_dir(self):
        return self.meta_dir / "build"

    @property
    def install_dir(self):
        return self.meta_dir / "install"

    @property
    def logs_dir(self):
        return self.meta_dir / "logs"

    @property
    def models_dir(self):
        return self.meta_dir / "models"

    @property
    def inputs_dir(self):
        return self.meta_dir / "inputs"

    @property
    def temp_dir(self):
        return self.meta_dir / "temp"

    @property
    def gen_dir(self):
        return self.meta_dir / "gen"

    @property
    def log_file_path(self):
        return self.logs_dir / "seal5.log"

    def check(self):
        pass

    def initialize(
        self,
        interactive: bool = False,
        clone: bool = False,
        clone_url: Optional[str] = None,
        clone_ref: Optional[str] = None,
        force: bool = False,
        verbose: bool = False,
    ):
        logger.info("Initializing Seal5")
        if not self.directory.is_dir():
            if clone is False and not ask_user("Clone LLVM repository?", default=False, interactive=interactive):
                logging.error(f"Target directory does not exist! Aborting...")
                sys.exit(1)
            clone_llvm_repo(self.directory, clone_url, ref=clone_ref)
        if self.meta_dir.is_dir():
            if force is False and not ask_user(
                "Overwrite existing .seal5 diretcory?", default=False, interactive=interactive
            ):
                logging.error(f"Directory {self.meta_dir} already exists! Aborting...")
                sys.exit(1)
        self.meta_dir.mkdir(exist_ok=True)
        create_seal5_directories(self.meta_dir, ["deps", "models", "logs", "build", "install", "temp", "inputs", "gen"])
        self.settings = Seal5Settings(data=DEFAULT_SETTINGS)
        self.settings.to_yaml_file(self.settings_file)
        set_log_file(self.log_file_path)
        set_log_level(
            console_level=self.settings.logging.console["level"], file_level=self.settings.logging.file["level"]
        )
        logger.info("Completed initialization of Seal5")

    def setup(
        self,
        interactive: bool = False,
        force: bool = False,
        verbose: bool = False,
    ):
        logger.info("Installing Seal5 dependencies")
        m2isar_dependency.clone(self.deps_dir / "M2-ISA-R", overwrite=force)
        # cdsl2llvm_dependency.clone(self.deps_dir / "cdsl2llvm", overwrite=force)
        logger.info("Completed installation of Seal5 dependencies")

    def load_cfg(self, file: Path, overwrite: bool = False):
        new_settings: Seal5Settings = Seal5Settings.from_yaml_file(file)
        self.settings.merge(new_settings, overwrite=overwrite)
        self.settings.to_yaml_file(self.settings_file)

    def prepare_environment(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = self.deps_dir / "M2-ISA-R"
        return env

    def parse_coredsl(self, file, out_dir, verbose: bool = False):
        args = [
            file,
            "-o",
            out_dir,
        ]
        utils.python("-m", "m2isar.frontends.coredsl2_seal5.parser", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def load_cdsl(self, file: Path, verbose: bool = False, overwrite: bool = False):
        assert file.is_file(), "TODO"
        filename: str = file.name
        dest = self.inputs_dir / filename
        if dest.is_file() and not overwrite:
            raise RuntimeError(f"File {filename} already loaded!")
        # Add file to inputs directory and settings
        utils.copy(file, dest)
        self.settings.data["inputs"].append(filename)
        # Parse CoreDSL file with M2-ISA-R (TODO: Standalone)
        dest = self.models_dir
        self.parse_coredsl(file, dest, verbose=verbose)
        self.settings.to_yaml_file(self.settings_file)

    def load(self, files: List[Path], verbose: bool = False, overwrite: bool = False):
        logger.info("Loading Seal5 inputs")
        for file in files:
            logger.info("Processing file: %s", file)
            ext = file.suffix
            if ext.lower() in [".yml", ".yaml"]:
                self.load_cfg(file, overwrite=overwrite)
            elif ext.lower() in [".core_desc"]:
                self.load_cdsl(file, verbose=verbose, overwrite=overwrite)
            else:
                raise RuntimeError(f"Unsupported input type: {ext}")
        # TODO: only allow single instr set for now and track inputs in settings
        logger.info("Compledted load of Seal5 inputs")

    def build(self, config="release", verbose: bool = False):
        logger.info("Building Seal5 LLVM")
        llvm_config = self.settings.llvm.configs.get(config, None)
        assert llvm_config is not None, f"Invalid llvm config: {config}"
        cmake_options = llvm_config["options"]
        build_llvm(self.directory, self.build_dir / config, cmake_options)
        logger.info("Completed build of Seal5 LLVM")

    def convert_models(self, verbose: bool = False, inplace: bool = False):
        assert not inplace
        input_files = list(self.models_dir.glob("*.m2isarmodel"))
        assert len(input_files) > 0, "No input models found!"
        for input_file in input_files:
            name = input_file.name
            base = input_file.stem
            new_name = f"{base}.seal5model"
            logger.info("Converting %s -> %s", name, new_name)
            args = [
                self.models_dir / name,
                "-o",
                self.models_dir / new_name,
                "--log",
                "info",
                # "debug",
            ]
        utils.python("-m", "m2isar.transform.seal5.converter", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def optimize_model(self, verbose: bool = False, inplace: bool = True):
        assert inplace
        input_files = list(self.models_dir.glob("*.seal5model"))
        assert len(input_files) > 0, "No Seal5 models found!"
        for input_file in input_files:
            name = input_file.name
            logger.info("Optimizing %s", name)
            args = [
                self.models_dir / name,
                "--log",
                "info",
                # "debug",
            ]
        utils.python("-m", "m2isar.transform.optimize_instructions.optimizer", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def filter_model(self, verbose: bool = False, inplace: bool = True):
        assert inplace
        input_files = list(self.models_dir.glob("*.seal5model"))
        assert len(input_files) > 0, "No Seal5 models found!"
        for input_file in input_files:
            name = input_file.name
            logger.info("Filtering %s", name)
            filter_settings = self.settings.filter
            filter_args = []
            def get_filter_args(data, suffix):
                if data is None:
                    return []
                ret = []
                keep = data.get("keep", None)
                drop = data.get("drop", None)
                if keep:
                    ret += [f"--keep-{suffix}", ",".join(keep)]
                if drop:
                    ret += [f"--drop-{suffix}", ",".join(drop)]
                return ret
            filter_sets = filter_settings.sets
            filter_instructions = filter_settings.instructions
            filter_aliases = filter_settings.aliases
            filter_intrinsics = filter_settings.intrinsics
            filter_args.extend(get_filter_args(filter_sets, "sets"))
            filter_args.extend(get_filter_args(filter_instructions, "instructions"))
            filter_args.extend(get_filter_args(filter_aliases, "aliases"))
            filter_args.extend(get_filter_args(filter_intrinsics, "intrinsics"))
            args = [
                self.models_dir / name,
                *filter_args,
                "--log",
                # "info",
                "debug",
            ]
        utils.python("-m", "m2isar.transform.filter_model.filter", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def drop_unused(self, verbose: bool = False, inplace: bool = True):
        assert inplace
        input_files = list(self.models_dir.glob("*.seal5model"))
        assert len(input_files) > 0, "No Seal5 models found!"
        for input_file in input_files:
            name = input_file.name
            logger.info("Dropping unused for %s", name)
            args = [
                self.models_dir / name,
                "--log",
                # "info",
                "debug",
            ]
        utils.python("-m", "m2isar.transform.drop_unused.optimizer", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def detect_registers(self, verbose: bool = False, inplace: bool = True):
        assert inplace
        input_files = list(self.models_dir.glob("*.seal5model"))
        assert len(input_files) > 0, "No Seal5 models found!"
        for input_file in input_files:
            name = input_file.name
            logger.info("Detecting registers for %s", name)
            args = [
                self.models_dir / name,
                "--log",
                "info",
                # "debug",
            ]
        utils.python("-m", "m2isar.transform.seal5.detect_registers", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)

    def detect_behavior_constraints(self, verbose: bool = False, inplace: bool = True):
        assert inplace
        input_files = list(self.models_dir.glob("*.seal5model"))
        assert len(input_files) > 0, "No Seal5 models found!"
        for input_file in input_files:
            name = input_file.name
            logger.info("Detecting registers for %s", name)
            args = [
                self.models_dir / name,
                "--log",
                # "info",
                "debug",
            ]
        utils.python("-m", "m2isar.transform.seal5.collect_raises.collect", *args, env=self.prepare_environment(), print_func=logger.info if verbose else logger.debug, live=True)


    def transform(self, verbose: bool = False):
        logger.info("Tranforming Seal5 models")
        inplace = True
        if not inplace:
            raise NotImplementedError()
        # TODO: flow.models: Seal5ModelWrapper -> transform(verbose: bool = False, *kwargs)
        # first convert M2-ISA-R MetaModel to Seal5 Metamodel
        self.convert_models(verbose=verbose)
        # filter model
        self.filter_model(verbose=verbose)
        # add aliases to model
        # self.add_aliases(verbose=verbose)
        # add intrinsics
        # self.add_intrinsics(verbose=verbose)
        # drop unused constants
        self.drop_unused(verbose=verbose)
        # optimize Seal5 Metamodel
        self.optimize_model(verbose=verbose)
        # detect registers
        self.detect_registers(verbose=verbose)
        # determine static constraints (xlen,...) -> subtargetvmap
        # self.detect_encoding_constraits(verbose=verbose)
        # determine dyn constraints (eliminate raise)
        self.detect_behavior_constraints(verbose=verbose)
        # add explicit constraints to cdsl
        # self.gen_explicit_constraints(verbose=verbose)
        # determine operand types
        self.collect_operand_types(verbose=verbose)
        # add explicit operand types to cdsl
        self.gen_explicit_operands(verbose=verbose)
        # detect ins/outs
        self.detect_inouts(verbose=verbose)
        # annotate ins/outs with attributes
        # self.annotate_inouts(verbose=verbose)
        # detect side effects
        self.detect_side_effects(verbose=verbose)
        # detect memory adressing modes
        self.detect_adressing_modes(verbose)

        # dump cdsl for each instr
        self.dump_cdsl_instrs(verbose=verbose)
        # generate llvm-ir behavior
        self.convert_behav_to_llvmir(verbose=verbose)
        # generate llvm-gmir behavior
        self.convert_llvmir_to_gmir(verbose=verbose)
        # detect legal GMIR ops (and map to selectiondag?)
        self.detect_legal_ops(verbose=verbose)
        # add legal ops to metamodel
        self.add_legalizer_settings(verbose=verbose)
        # extract costs/heuristics
        self.extract_costs_and_heuristics(verbose)

        logger.info("Completed tranformation of Seal5 models")

    def generate(self, verbose: bool = False):
        logger.info("Generating Seal5 patches")
        # raise NotImplementedError
        patches = []
        skip = []  # TODO: User, Global, PerInstr
        # TODO: only: []

        # General
        if "subtarget_features" not in skip:
            patches.extend(self.gen_subtarget_feature_patches())
        if "subtarget_tests" not in skip:
            patches.extend(self.gen_subtarget_tests_patches())

        # MC Level
        if "register_types" not in skip:
            patches.extend(self.gen_register_types_patches())
        if "operand_types" not in skip:
            patches.extend(self.gen_operand_types_patches())
        if "instruction_formats" not in skip:
            patches.extend(self.gen_instruction_format_patches())
        if "instruction_infos" not in skip:
            patches.extend(self.gen_instruction_infos_patches())
        if "disassembler" not in skip:
            patches.extend(self.gen_disassembler_patches())
        if "mc_tests" not in skip:
            patches.extend(self.gen_mc_tests_patches())

        # Codegen Level
        if "selection_dag_legalizer" not in skip:
            patches.extend(self.gen_selection_dag_legalizer_patches())
        if "globalisel_legalizer" not in skip:
            patches.extend(self.gen_globalisel_legalizer_patches())
        if "scalar_costs" not in skip:
            patches.extend(self.gen_scalar_costs_patches())
        if "simd_costs" not in skip:
            patches.extend(self.gen_simd_costs_patches())
        if "isel_patterns" not in skip:
            patches.extend(self.gen_isel_patterns_patches())
        if "codegen_test" not in skip:
            patches.extend(self.gen_codegen_tests_patches())

        logger.info("Completed generation of Seal5 patches")

    def collect_patches(self, stage: Optional[PatchStage]):
        return []  # TODO

    def patch(self, verbose: bool = False, stages: List[PatchStage] = None):
        logger.info("Applying Seal5 patches")
        # raise NotImplementedError
        if stages is None:
            stages = list(map(PatchStage, range(PatchStage.PHASE_4)))
        assert len(stages) > 0
        for stage in stages:
            logger.info("Current stage: %s", stage)
            patches = self.collect_patches(stage=stage)
            print("patches", patches)
        logger.info("Completed application of Seal5 patches")

    def test(self, debug: bool = False, verbose: bool = False, ignore_error: bool = False):
        logger.info("Testing Seal5 LLVM")
        name = "debug" if debug else "release"
        test_paths = self.settings.test.paths
        failing_tests = test_llvm(self.directory / "llvm" / "test", self.build_dir / name, test_paths, verbose=verbose)
        if len(failing_tests) > 0:
            logger.error("%d tests failed: %s", len(failing_tests), ", ".join(failing_tests))
            if not ignore_error:
                raise RuntimeError("Tests failed!")
        logger.info("Completed test of Seal5 LLVM")

    def deploy(self, verbose: bool = False):
        logger.info("Deploying Seal5 LLVM")
        logger.info("Completed deployment of Seal5 LLVM")

    def export(self, dest: Path, verbose: bool = False):
        logger.info("Exporting Seal5 artifacts")
        if isinstance(dest, str):
            dest = Path(dest)
        suffix = dest.suffix
        if suffix != ".gz":
            raise NotImplementedError("Only .tar.gz export is supported!")
        artifacts = [self.inputs_dir, self.gen_dir, self.models_dir, self.logs_dir, self.settings_file]
        with tarfile.open(dest, mode='w:gz') as archive:
            for artifact in artifacts:
                name = str(artifact)
                assert str(self.meta_dir) in name
                name = name.replace(f"{self.meta_dir}/", "")
                if artifact.is_file():
                    archive.add(artifact, arcname=name)
                elif artifact.is_dir():
                    archive.add(artifact, arcname=name, recursive=True)

        logger.info("Completed export of Seal5 artifacts")

    def clean(self, verbose: bool = False, interactive: bool = False):
        logger.info("Cleaning Seal5 directories")
        raise NotImplementedError
        to_clean = [
            self.temp_dir,
            self.gen_dir,
            self.models_dir,
            self.inputs_dir,
            self.logs_dir,
            self.install_dir,
            self.build_dir,
            self.deps_dir,
        ]
        for path in to_clean:
            clean_path(path, interactive=interactive)
        self.settings.data["inputs"] = []
        logger.info("Completed clean of Seal5 directories")
