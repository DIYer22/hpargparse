# logistics
import subprocess
import sys
import ast

import argparse
import dill
import yaml
import os
from copy import deepcopy

from types import MethodType

import hpman
from hpman import (
    HyperParameterManager,
    HyperParameterOccurrence,
    SourceHelper,
    L,
    EmptyValue,
)
from tabulate import tabulate

from . import config


from typing import Union, List


def list_of_dict2tab(list_of_dict, headers):
    """Convert "a list of dict" to "a list of list" that suitable
    for table processing libraries (such as tabulate)

    :params list_of_dict: input data
    :params headers: a list of str, header items with order

    :return: list of list of objects
    """
    rows = [[dct[h] for h in headers] for dct in list_of_dict]
    return rows


def make_detail_str(details):
    """
    :param details: List of details. A detail is either a string
        or a list of strings, each one comprises a line

    :return: a string, the formatted detail
    """
    strs = []
    for d in details:

        if isinstance(d["detail"], str):
            ds = [d["detail"]]
        else:
            assert isinstance(d["detail"], (list, tuple)), d["detail"]
            ds = d["detail"]

        s = "\n".join(["{}:".format(d["name"])] + ["  " + line for line in ds])
        strs.append(s)
        strs.append("\n")

    return "".join(strs)


def make_value_illu(v):
    """Mute non-literal-evaluable values

    :return: None if v is :class:`hpman.NotLiteralEvaluable`,
        otherwise the original input.
    """
    if isinstance(v, hpman.NotLiteralEvaluable):
        return None
    return v


def hp_list(mgr):
    """Print hyperparameter settings to stdout
    """
    print("All hyperparameters:")
    print("    {}".format(sorted(mgr.get_values().keys())))

    rows = []
    for k, d in sorted(mgr.db.group_by("name").items()):
        details = []
        for i, oc in enumerate(
            d.select(L.exist_attr("filename")).sorted(L.order_by("filename"))
        ):
            # make context detail
            details.append(
                {
                    "name": "occurrence[{}]".format(i),
                    "detail": SourceHelper.format_given_filepath_and_lineno(
                        oc.filename, oc.lineno
                    ),
                }
            )

        # combine details
        detail_str = make_detail_str(details)
        oc = d.sorted(L.value_priority)[0]
        row = {
            "name": k,
            "type": type(oc.value).__name__,
            "value": make_value_illu(oc.value),
            "details": detail_str,
        }

        rows.append(row)

    headers = ["name", "type", "value", "details"]
    data = list_of_dict2tab(rows, headers)

    print("Details:")
    s = tabulate(data, headers=headers, tablefmt="grid")
    print(s)


def parse_action_list(inject_actions: Union[bool, List[str]]) -> List[str]:
    """Parse inputs to inject actions.

    :param inject_actions: see :func:`.bind` for detail
    :return: a list of action names
    """
    if isinstance(inject_actions, bool):
        inject_actions = {True: ["save", "load", "list"], False: []}[inject_actions]
    return inject_actions


def _get_argument_type_by_value(value):
    typ = type(value)
    print("@get_type:", typ)
    if isinstance(value, (list, dict)):

        def type_func(s):
            if isinstance(s, typ):
                eval_val = s
            else:
                assert isinstance(s, str), type(s)
                eval_val = ast.literal_eval(s)

            if not isinstance(eval_val, typ):
                raise TypeError("value `{}` is not of type {}".format(eval_val, typ))
            return eval_val

        return type_func
    return typ


def str2bool(v):
    """Parsing a string into a bool type.

    :param v: A string that needs to be parsed.

    :return: True or False
    """
    if v.lower() in ["yes", "true", "t", "y", "1"]:
        return True
    elif v.lower() in ["no", "false", "f", "n", "0"]:
        return False
    else:
        raise argparse.ArgumentTypeError("Unsupported value encountered.")


def inject_args(
    parser: argparse.ArgumentParser,
    hp_mgr: hpman.HyperParameterManager,
    *,
    inject_actions: List[str],
    action_prefix: str,
    serial_format: str,
    show_defaults: bool,
) -> argparse.ArgumentParser:
    """Inject hpman parsed hyperparameter settings into argparse arguments.
    Only a limited set of format are supported. See code for details.

    :param parser: Use given parser object of `class`:`argparse.ArgumentParser`.
    :param hp_mgr: A `class`:`hpman.HyperParameterManager` object.

    :param inject_actions: A list of actions names to inject
    :param action_prefix: Prefix for hpargparse related options
    :param serial_format: One of 'yaml' and 'pickle'
    :param show_defaults: Show default values

    :return: The injected parser.
    """

    help = ""
    if show_defaults:
        parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter

        # Default value will be shown when using argparse.ArgumentDefaultsHelpFormatter
        # only if a help message is present. This is the behavior of argparse.
        help = " "

    # add options for collected hyper-parameters
    for k, v in hp_mgr.get_values().items():
        # this is just a simple hack
        option_name = "--{}".format(k.replace("_", "-"))

        if _get_argument_type_by_value(v) == bool:
            # argparse does not directly support bool types.
            parser.add_argument(
                option_name, type=str2bool, default=v, help=help  # EmptyValue(),
            )
        else:
            parser.add_argument(
                option_name,
                type=_get_argument_type_by_value(v),
                default=v,  # EmptyValue()
                help=help,
            )

    make_option = lambda name: "--{}-{}".format(action_prefix, name)

    for action in inject_actions:
        if action == "list":
            parser.add_argument(
                make_option("list"),
                action="store",
                default=False,
                const="yaml",
                nargs="?",
                choices=["detail", "yaml"],
                help=(
                    "List all available hyperparameters. If `{} detail` is"
                    " specified, a verbose table will be print"
                ).format(make_option("list")),
            )
        elif action == "save":
            parser.add_argument(
                make_option("save"),
                help=(
                    "Save hyperparameters to a file. The hyperparameters"
                    " are saved after processing of all other options"
                ),
            )

        elif action == "load":
            parser.add_argument(
                make_option("load"),
                help=(
                    "Load hyperparameters from a file. The hyperparameters"
                    " are loaded before any other options are processed"
                ),
            )

    if "load" in inject_actions or "save" in inject_actions:
        parser.add_argument(
            make_option("serial-format"),
            default=serial_format,
            choices=config.HP_SERIAL_FORMAT_CHOICES,
            help=(
                "Format of the saved config file. Defaults to {}."
                " Can be set to override auto file type deduction."
            ).format(serial_format),
        )

    if inject_actions:
        parser.add_argument(
            make_option("exit"),
            action="store_true",
            help="process all hpargparse actions and quit",
        )

    return parser


def _infer_file_format(path):
    name, ext = os.path.splitext(path)
    supported_exts = {
        ".yaml": "yaml",
        ".yml": "yaml",
        ".pickle": "pickle",
        ".pkl": "pickle",
    }

    if ext in supported_exts:
        return supported_exts[ext]
    raise ValueError(
        "Unsupported file extension: {} of path {}".format(ext, path),
        "Supported file extensions: {}".format(
            ", ".join("`{}`".format(i) for i in sorted(supported_exts))
        ),
    )


def hp_save(path: str, hp_mgr: hpman.HyperParameterManager, serial_format: str):
    """Save(serialize) hyperparamters.

    :param path: Where to save
    :param hp_mgr: The HyperParameterManager to be saved.
    :param serial_format: The saving format.

    :see: :func:`.bind` for more detail.
    """
    values = hp_mgr.get_values()

    if serial_format == "auto":
        serial_format = _infer_file_format(path)

    if serial_format == "yaml":
        with open(path, "w") as f:
            yaml.dump(values, f)
    else:
        assert serial_format == "pickle", serial_format
        with open(path, "wb") as f:
            dill.dump(values, f)


def hp_load(path, hp_mgr, serial_format):
    """Load(deserialize) hyperparamters.

    :param path: Where to load
    :param hp_mgr: The HyperParameterManager to be set.
    :param serial_format: The saving format.

    :see: :func:`.bind` for more detail.
    """
    if serial_format == "auto":
        serial_format = _infer_file_format(path)

    if serial_format == "yaml":
        with open(path, "r") as f:
            values = yaml.safe_load(f)
    else:
        assert serial_format == "pickle", serial_format
        with open(path, "rb") as f:
            values = dill.load(f)

    old_values = hp_mgr.get_values()
    for k, v in values.items():
        if k in old_values:
            old_v = old_values[k]
            try:
                _get_argument_type_by_value(old_v)(v)
            except TypeError as e:
                e.args = ("Error parsing hyperparameter `{}`".format(k),) + e.args
                raise

    hp_mgr.set_values(values)


def bind(
    parser: argparse.ArgumentParser,
    hp_mgr: hpman.HyperParameterManager,
    *,
    inject_actions: Union[bool, List[str]] = True,
    action_prefix: str = config.HP_ACTION_PREFIX_DEFAULT,
    serial_format: str = config.HP_SERIAL_FORMAT_DEFAULT,
    show_defaults: bool = True,
):
    """Bridging the gap between argparse and hpman. This is
        the most important method. Once bounded, hpargparse
        will do the rest for you.

    :param parser: A `class`:`argparse.ArgumentParser` object
    :param hp_mgr: The hyperparameter manager from `hpman`. It is
        usually an 'underscore' variable obtained by `from hpman.m import _`
    :param inject_actions: A list of actions names to inject, or True, to
        inject all available actions. Available actions are 'save', 'load', and
        'list'
    :param action_prefix: Prefix for options of hpargparse injected additional
        actions. e.g., the default action_prefix is 'hp'. Therefore, the
        command line options added by :func:`.bind` will be '--hp-save',
        '--hp-load', '--hp-list', etc.
    :param serial_format: One of 'auto', 'yaml' and 'pickle'. Defaults to
        'auto'.  In most cases you need not to alter this argument as long as
        you give the right file extension when using save and load action. To
        be specific, '.yaml' and '.yml' would be deemed as yaml format, and
        '.pickle' and '.pkl' would be seen as pickle format.
    :param show_defaults: Show the default value in help messages.

    :note: pickle is done by `dill` to support pickling of more types.
    """

    # make action list to be injected
    inject_actions = parse_action_list(inject_actions)

    inject_args(
        parser,
        hp_mgr,
        inject_actions=inject_actions,
        action_prefix=action_prefix,
        serial_format=serial_format,
        show_defaults=show_defaults,
    )

    # hook parser.parse_args
    parser._original_parse_args = parser.parse_args

    def new_parse_args(self, *args, **kwargs):
        args = self._original_parse_args(*args, **kwargs)

        get_action_value = lambda name: getattr(
            args, "{}_{}".format(action_prefix, name)
        )

        # load saved hyperparameter instance
        load_value = get_action_value("load")
        if "load" in inject_actions and load_value is not None:
            hp_load(load_value, hp_mgr, serial_format)

        # set hyperparameters set from command lines
        for k, v in hp_mgr.get_values().items():
            if hasattr(args, k):
                t = getattr(args, k)
                if isinstance(t, EmptyValue):
                    continue
                hp_mgr.set_value(k, t)

        save_value = get_action_value("save")
        if "save" in inject_actions and save_value is not None:
            hp_save(save_value, hp_mgr, serial_format)

        if "list" in inject_actions and args.hp_list:
            if args.hp_list == "yaml":
                print(yaml.dump(hp_mgr.get_values()), end="")
            else:
                assert args.hp_list == "detail", args.hp_list
                hp_list(hp_mgr)

            sys.exit(0)

        if inject_actions and get_action_value("exit"):
            sys.exit(0)

        return args

    parser.parse_args = MethodType(new_parse_args, parser)
