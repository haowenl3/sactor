import os
import subprocess

import re
from clang import cindex
from clang.cindex import Cursor

from sactor import utils
from sactor.utils import get_temp_dir

from .c_parser import CParser


def _remove_static_decorator_impl(node: Cursor, source_code: str) -> str:
    code_lines = source_code.split("\n")
    tokens = utils.cursor_get_tokens(node)
    first_token = next(tokens)
    if first_token.spelling == "static":
        # delete the static keyword
        start_line = first_token.extent.start.line - 1
        start_column = first_token.extent.start.column
        end_column = first_token.extent.end.column
        code_lines[start_line] = code_lines[start_line][:start_column-1] + code_lines[start_line][end_column:]

    return "\n".join(code_lines)

def _is_empty(node: Cursor) -> bool:
    tokens = utils.cursor_get_tokens(node)
    token_spellings = [token.spelling for token in tokens]
    return len(token_spellings) == 0

def remove_function_static_decorator(function_name: str, source_code: str) -> str:
    """
    Removes `static` decorator from the ident in the source code.
    """
    tmpdir = get_temp_dir()
    with open(os.path.join(tmpdir, "tmp.c"), "w") as f:
        f.write(source_code)

    c_parser = CParser(os.path.join(tmpdir, "tmp.c"))

    function = c_parser.get_function_info(function_name)
    node = function.node

    # handle declaration node
    decl_node = function.get_declaration_node()
    if decl_node is not None and not _is_empty(decl_node):
        source_code = _remove_static_decorator_impl(decl_node, source_code)
        # Need to parse the source code again to get the updated node
        with open(os.path.join(tmpdir, "tmp.c"), "w") as f:
            f.write(source_code)

        c_parser = CParser(os.path.join(tmpdir, "tmp.c"), omit_error=True)
        node = c_parser.get_function_info(function_name).node

    if node is None:
        raise ValueError("Node is None")

    removed_code = _remove_static_decorator_impl(node, source_code)

    # remove the tmp.c
    os.remove(os.path.join(tmpdir, "tmp.c"))

    return removed_code

def expand_all_macros(input_file):
    filename = os.path.basename(input_file)
    with open(input_file, 'r') as f:
        content = f.read()
    # Find the last #include line and insert the marker
    lines = content.splitlines()

    # remove #ifdef __cplusplus
    start = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('#ifdef __cplusplus'):
            start = i
        if line.strip().startswith('#endif'):
            if start != -1:
                lines = lines[:start] + lines[i+1:]

    removed_includes = []
    for i, line in enumerate(lines):
        if line.strip().startswith('#include'):
            # remove the include line, to prevent expand macros in the header
            removed_includes.append(line)
            lines[i] = ''

    tmpdir = utils.get_temp_dir()
    os.makedirs(tmpdir, exist_ok=True)

    # Write to a temporary file
    with open(os.path.join(tmpdir, filename), 'w') as f:
        f.write('\n'.join(lines))

    # use `cpp -C -P` to expand all macros
    result = subprocess.run(
        ['cpp', '-C', '-P', os.path.join(tmpdir, filename)],
        capture_output=True,
        text=True,
        check=True
    )

    # Combine with expanded part
    expanded = '\n'.join(removed_includes) + '\n' + result.stdout
    out_path = os.path.join(tmpdir, f"expanded_{filename}")

    with open(out_path, 'w') as f:
        f.write(expanded)

    # check if it can compile, if not, will raise an error
    utils.compile_c_executable(out_path)

    return out_path

def preprocess_source_code(input_file):
    # Expand all macros in the input file
    expanded_file = expand_all_macros(input_file)
    # Unfold all typedefs in the expanded file
    unfolded_file = unfold_typedefs(expanded_file)
    return unfolded_file


def unfold_typedefs(input_file):
    c_parser = CParser(input_file, omit_error=True)
    type_aliases = c_parser._type_alias
    typedef_nodes = c_parser.get_typedef_nodes()

    with open(input_file, 'r') as f:
        lines = f.readlines()

    lines_to_remove = set()
    for node in typedef_nodes:
        for i in range(node.extent.start.line - 1, node.extent.end.line):
            lines_to_remove.add(i)

    modified_lines = []
    for i, line in enumerate(lines):
        if i not in lines_to_remove:
            modified_lines.append(line)
        else:
            modified_lines.append("\n")

    code = "".join(modified_lines)

    # replace all occurrences of the alias with the original type.
    for alias, original in type_aliases.items():
        # This regex replaces all occurrences of the alias with the original type.
        # It uses word boundaries (\b) to ensure that it only replaces whole words,
        # preventing it from replacing parts of other identifiers.
        pattern = rf"""\b{re.escape(alias)}\b"""
        code = re.sub(pattern, original, code)

    with open(input_file, 'w') as f:
        f.write(code)

    return input_file
