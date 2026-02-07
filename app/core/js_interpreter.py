"""
JavaScript interpreter for YouTube cipher decryption and nsig (throttle) bypass.
Ported and simplified from yt-dlp's jsinterp.py.

This interpreter handles the subset of JavaScript needed to execute YouTube's
signature decryption and n-parameter transformation functions. It supports:
- Variable assignments and declarations (var/let/const)
- Function definitions and calls
- Array operations (push, pop, splice, reverse, slice, indexOf, join)
- String operations (split, reverse, join, charAt, charCodeAt, indexOf, slice, length)
- Object property access and method calls
- Arithmetic and bitwise operators
- Ternary operator
- for/while loops
- if/else statements
- try/catch
- Regular expressions (basic)
- parseInt, Math.* functions
"""

import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

_NAME_RE = r"[a-zA-Z_$][\w$]*"


class JSUndefined:
    """Represents JavaScript's undefined value."""

    def __repr__(self):
        return "undefined"

    def __bool__(self):
        return False


JS_UNDEFINED = JSUndefined()


class JSInterpreterError(Exception):
    pass


class JSBreak(Exception):
    pass


class JSContinue(Exception):
    pass


def _js_ternary(val):
    """Evaluate JS truthiness."""
    if val is None or val is JS_UNDEFINED or val is False or val == 0 or val == "" or val == 0.0:
        return False
    try:
        if math.isnan(val):
            return False
    except (TypeError, ValueError):
        pass
    return True


def _to_number(val):
    """Convert value to a number like JavaScript would."""
    if val is None or val is JS_UNDEFINED:
        return 0
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return float("nan")
    return 0


class JSInterpreter:
    """
    Simplified JavaScript interpreter for YouTube player code execution.

    Designed to handle the specific patterns found in YouTube's cipher
    and nsig transformation functions.
    """

    def __init__(self, code: str, objects: dict | None = None):
        self.code = code
        self._functions: dict[str, tuple[list[str], str]] = {}
        self._objects: dict[str, dict] = objects or {}

    def extract_function(self, func_name: str) -> Any:
        """
        Extract a named function from the code and return a callable.
        """
        func_name_re = re.escape(func_name)

        # Try: var funcName = function(args) { body }
        # or:  function funcName(args) { body }
        # or:  funcName = function(args) { body }
        func_match = re.search(
            rf"(?:function\s+{func_name_re}|"
            rf"(?:var\s+)?{func_name_re}\s*=\s*function)"
            rf"\s*\((?P<args>[^)]*)\)\s*\{{",
            self.code,
        )

        if not func_match:
            raise JSInterpreterError(f"Could not find function {func_name!r}")

        args_str = func_match.group("args")
        arg_names = [a.strip() for a in args_str.split(",") if a.strip()]

        # Find matching closing brace
        body_start = func_match.end()
        body = self._find_matching_brace(self.code, body_start - 1)

        self._functions[func_name] = (arg_names, body)

        def call_func(*args):
            return self._call_function(func_name, args)

        return call_func

    def extract_function_code(self, func_name: str) -> tuple[list[str], str]:
        """Extract function argument names and body code."""
        if func_name in self._functions:
            return self._functions[func_name]

        func_name_re = re.escape(func_name)

        func_match = re.search(
            rf"(?:function\s+{func_name_re}|"
            rf"(?:var\s+)?{func_name_re}\s*=\s*function)"
            rf"\s*\((?P<args>[^)]*)\)\s*\{{",
            self.code,
        )

        if not func_match:
            raise JSInterpreterError(f"Could not find function {func_name!r}")

        args_str = func_match.group("args")
        arg_names = [a.strip() for a in args_str.split(",") if a.strip()]

        body_start = func_match.end()
        body = self._find_matching_brace(self.code, body_start - 1)

        self._functions[func_name] = (arg_names, body)
        return arg_names, body

    def extract_object(self, obj_name: str) -> dict:
        """Extract an object and its methods from the code."""
        if obj_name in self._objects:
            return self._objects[obj_name]

        obj_name_re = re.escape(obj_name)
        obj_match = re.search(
            rf"(?:var\s+)?{obj_name_re}\s*=\s*\{{",
            self.code,
        )

        if not obj_match:
            raise JSInterpreterError(f"Could not find object {obj_name!r}")

        obj_body = self._find_matching_brace(self.code, obj_match.end() - 1)

        obj = {}
        # Parse method definitions: name: function(args) { body }
        for m in re.finditer(
            rf"(?P<key>{_NAME_RE})\s*:\s*function\s*\((?P<args>[^)]*)\)\s*\{{",
            obj_body,
        ):
            key = m.group("key")
            args = [a.strip() for a in m.group("args").split(",") if a.strip()]
            # Find body
            body_start_in_obj = m.end() - 1
            # We need to find the body within obj_body
            obj_body.find(
                "{", body_start_in_obj - len(obj_body) if body_start_in_obj > len(obj_body) else 0
            )
            func_body = self._find_matching_brace(
                obj_body, m.end() - 1 - (len(obj_body) - len(obj_body))
            )

            # Actually, let's re-find from the method position
            remaining = obj_body[m.start() :]
            brace_pos = remaining.find("{", remaining.find(")"))
            if brace_pos >= 0:
                func_body = self._find_matching_brace(remaining, brace_pos)
                obj[key] = (args, func_body)

        self._objects[obj_name] = obj
        return obj

    def _find_matching_brace(self, code: str, start: int) -> str:
        """Find the content between matching braces starting at position start."""
        if start >= len(code) or code[start] != "{":
            # Try to find the next opening brace
            idx = code.find("{", start)
            if idx < 0:
                raise JSInterpreterError("Could not find opening brace")
            start = idx

        depth = 0
        in_string = None
        escape = False

        for i in range(start, len(code)):
            c = code[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c in ('"', "'", "`") and in_string is None:
                in_string = c
            elif c == in_string:
                in_string = None
            elif in_string is None:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return code[start + 1 : i]

        raise JSInterpreterError("Could not find matching closing brace")

    def _call_function(self, func_name: str, args: tuple) -> Any:
        """Call a previously extracted function."""
        if func_name not in self._functions:
            self.extract_function_code(func_name)

        arg_names, body = self._functions[func_name]
        local_vars = {}
        for i, name in enumerate(arg_names):
            if i < len(args):
                local_vars[name] = args[i]
            else:
                local_vars[name] = JS_UNDEFINED

        return self._interpret_block(body, local_vars)

    def _interpret_block(self, code: str, local_vars: dict) -> Any:
        """Interpret a block of statements."""
        statements = self._split_statements(code)
        result = None

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            result, should_return = self._interpret_statement(stmt, local_vars)
            if should_return:
                return result

        return result

    def _split_statements(self, code: str) -> list[str]:
        """Split code into individual statements, respecting braces and strings."""
        statements = []
        current = []
        depth = 0
        in_string = None
        escape = False

        i = 0
        while i < len(code):
            c = code[i]

            if escape:
                current.append(c)
                escape = False
                i += 1
                continue

            if c == "\\" and in_string:
                current.append(c)
                escape = True
                i += 1
                continue

            if c in ('"', "'", "`") and in_string is None:
                in_string = c
                current.append(c)
            elif c == in_string:
                in_string = None
                current.append(c)
            elif in_string:
                current.append(c)
            elif c in ("{", "(", "["):
                depth += 1
                current.append(c)
            elif c in ("}", ")", "]"):
                depth -= 1
                current.append(c)
            elif c == ";" and depth == 0:
                statements.append("".join(current))
                current = []
            else:
                current.append(c)

            i += 1

        if current:
            remaining = "".join(current).strip()
            if remaining:
                statements.append(remaining)

        return statements

    def _interpret_statement(self, stmt: str, local_vars: dict) -> tuple[Any, bool]:
        """
        Interpret a single statement. Returns (value, should_return).
        """
        stmt = stmt.strip()
        if not stmt:
            return None, False

        # Return statement
        if stmt.startswith("return"):
            expr = stmt[6:].strip()
            if expr.startswith(";"):
                expr = expr[1:].strip()
            if not expr:
                return None, True
            return self._interpret_expression(expr, local_vars), True

        # Variable declaration
        m = re.match(r"(?:var|let|const)\s+", stmt)
        if m:
            decl = stmt[m.end() :]
            # Handle multiple declarations: var a = 1, b = 2
            for part in self._comma_split(decl):
                part = part.strip()
                eq_pos = part.find("=")
                if eq_pos > 0:
                    name = part[:eq_pos].strip()
                    value = self._interpret_expression(part[eq_pos + 1 :].strip(), local_vars)
                    local_vars[name] = value
                else:
                    local_vars[part] = JS_UNDEFINED
            return None, False

        # If statement
        if stmt.startswith("if"):
            return self._handle_if(stmt, local_vars)

        # For loop
        if stmt.startswith("for"):
            return self._handle_for(stmt, local_vars)

        # While loop
        if stmt.startswith("while"):
            return self._handle_while(stmt, local_vars)

        # Try/catch
        if stmt.startswith("try"):
            return self._handle_try(stmt, local_vars)

        # Block statement
        if stmt.startswith("{") and stmt.endswith("}"):
            return self._interpret_block(stmt[1:-1], local_vars), False

        # Expression statement (including assignments)
        result = self._interpret_expression(stmt, local_vars)
        return result, False

    def _interpret_expression(self, expr: str, local_vars: dict) -> Any:
        """Interpret a JavaScript expression."""
        expr = expr.strip()
        if not expr:
            return JS_UNDEFINED

        # String literals
        if (expr.startswith('"') and expr.endswith('"')) or (
            expr.startswith("'") and expr.endswith("'")
        ):
            try:
                return json.loads(expr if expr[0] == '"' else f'"{expr[1:-1]}"')
            except json.JSONDecodeError:
                return expr[1:-1]

        # Numeric literals
        if re.match(r"^-?\d+$", expr):
            return int(expr)
        if re.match(r"^-?\d+\.\d*$", expr):
            return float(expr)
        if expr.startswith("0x"):
            return int(expr, 16)

        # Boolean/null/undefined
        if expr == "true":
            return True
        if expr == "false":
            return False
        if expr == "null":
            return None
        if expr == "undefined":
            return JS_UNDEFINED
        if expr == "NaN":
            return float("nan")
        if expr == "Infinity":
            return float("inf")

        # Array literal
        if expr.startswith("[") and expr.endswith("]"):
            inner = expr[1:-1].strip()
            if not inner:
                return []
            items = self._comma_split(inner)
            return [self._interpret_expression(item.strip(), local_vars) for item in items]

        # Parenthesized expression
        if expr.startswith("("):
            inner, rest = self._find_matching_paren(expr)
            val = self._interpret_expression(inner, local_vars)
            if rest:
                # Method call on result, property access, etc.
                return self._handle_postfix(val, rest, local_vars)
            return val

        # Unary operators
        if expr.startswith("!"):
            val = self._interpret_expression(expr[1:], local_vars)
            return not _js_ternary(val)

        if expr.startswith("typeof "):
            val = self._interpret_expression(expr[7:], local_vars)
            if val is JS_UNDEFINED:
                return "undefined"
            if val is None:
                return "object"
            if isinstance(val, bool):
                return "boolean"
            if isinstance(val, (int, float)):
                return "number"
            if isinstance(val, str):
                return "string"
            if callable(val):
                return "function"
            return "object"

        # Ternary operator
        ternary_parts = self._split_ternary(expr)
        if ternary_parts:
            cond, if_true, if_false = ternary_parts
            cond_val = self._interpret_expression(cond, local_vars)
            if _js_ternary(cond_val):
                return self._interpret_expression(if_true, local_vars)
            return self._interpret_expression(if_false, local_vars)

        # Binary operators (lowest precedence first)
        for ops in [
            ["||"],
            ["&&"],
            ["|"],
            ["^"],
            ["&"],
            ["===", "!==", "==", "!="],
            ["<=", ">=", "<", ">"],
            [">>", "<<", ">>>"],
            ["+", "-"],
            ["*", "/", "%"],
        ]:
            result = self._try_binary_op(expr, ops, local_vars)
            if result is not None:
                return result[0]

        # Assignment
        assign_match = re.match(
            rf"({_NAME_RE})(?:\[(.+?)\])?\s*([-+*/%&|^]?=)\s*(.+)$",
            expr,
            re.DOTALL,
        )
        if assign_match:
            name = assign_match.group(1)
            index_expr = assign_match.group(2)
            op = assign_match.group(3)
            val_expr = assign_match.group(4)

            val = self._interpret_expression(val_expr, local_vars)

            if op != "=":
                old_val = local_vars.get(name, 0)
                if index_expr is not None:
                    idx = self._interpret_expression(index_expr, local_vars)
                    old_val = old_val[int(idx)]
                val = self._apply_op(op[:-1], old_val, val)

            if index_expr is not None:
                idx = self._interpret_expression(index_expr, local_vars)
                local_vars[name][int(idx)] = val
            else:
                local_vars[name] = val
            return val

        # Pre/post increment/decrement
        m = re.match(rf"(\+\+|--)({_NAME_RE})", expr)
        if m:
            op, name = m.group(1), m.group(2)
            local_vars[name] = local_vars.get(name, 0) + (1 if op == "++" else -1)
            return local_vars[name]

        m = re.match(rf"({_NAME_RE})(\+\+|--)", expr)
        if m:
            name, op = m.group(1), m.group(2)
            old = local_vars.get(name, 0)
            local_vars[name] = old + (1 if op == "++" else -1)
            return old

        # parseInt
        m = re.match(r"parseInt\((.+?)(?:,\s*(.+?))?\)$", expr)
        if m:
            val = self._interpret_expression(m.group(1), local_vars)
            radix = int(self._interpret_expression(m.group(2), local_vars)) if m.group(2) else 10
            try:
                return int(str(val), radix)
            except (ValueError, TypeError):
                return float("nan")

        # String.fromCharCode
        m = re.match(r"String\.fromCharCode\((.+)\)$", expr)
        if m:
            args = self._comma_split(m.group(1))
            chars = [chr(int(self._interpret_expression(a.strip(), local_vars))) for a in args]
            return "".join(chars)

        # Math functions
        m = re.match(r"Math\.(\w+)\((.+)\)$", expr)
        if m:
            func_name = m.group(1)
            args = [
                self._interpret_expression(a.strip(), local_vars)
                for a in self._comma_split(m.group(2))
            ]
            math_funcs = {
                "abs": math.fabs,
                "ceil": math.ceil,
                "floor": math.floor,
                "round": round,
                "max": max,
                "min": min,
                "pow": math.pow,
                "sqrt": math.sqrt,
                "log": math.log,
                "sign": lambda x: (x > 0) - (x < 0),
                "random": lambda: 0.5,  # Deterministic for reproducibility
            }
            if func_name in math_funcs:
                return math_funcs[func_name](*args)

        # Math constants
        if expr == "Math.PI":
            return math.pi
        if expr == "Math.E":
            return math.e

        # Function call
        m = re.match(rf"({_NAME_RE})\((.*)?\)$", expr, re.DOTALL)
        if m:
            func_name = m.group(1)
            args_str = m.group(2) or ""
            args = (
                [
                    self._interpret_expression(a.strip(), local_vars)
                    for a in self._comma_split(args_str)
                ]
                if args_str.strip()
                else []
            )

            # Check local function
            if func_name in local_vars and callable(local_vars[func_name]):
                return local_vars[func_name](*args)

            # Check extracted functions
            if func_name in self._functions:
                return self._call_function(func_name, tuple(args))

            # Try to extract from code
            try:
                self.extract_function_code(func_name)
                return self._call_function(func_name, tuple(args))
            except JSInterpreterError:
                pass

        # Property access and method calls on variables
        # e.g., a.split(""), a.length, obj.func(args)
        m = re.match(
            rf"({_NAME_RE})((?:\.[{_NAME_RE[1:]}]+|\[.+?\])+(?:\(.*\))?.*)", expr, re.DOTALL
        )
        if m:
            obj_name = m.group(1)
            chain = m.group(2)

            obj = local_vars.get(obj_name, JS_UNDEFINED)
            if obj is JS_UNDEFINED:
                # Try to load as an object from code
                try:
                    obj = self.extract_object(obj_name)
                except JSInterpreterError:
                    obj = JS_UNDEFINED

            if obj is not JS_UNDEFINED:
                return self._resolve_chain(obj, chain, local_vars)

        # Simple variable lookup
        if re.match(rf"^{_NAME_RE}$", expr):
            return local_vars.get(expr, JS_UNDEFINED)

        # If all else fails
        logger.debug(f"Could not interpret expression: {expr[:100]}")
        return JS_UNDEFINED

    def _resolve_chain(self, obj: Any, chain: str, local_vars: dict) -> Any:
        """Resolve a chain of property accesses and method calls."""
        while chain:
            chain = chain.strip()
            if not chain:
                break

            # .property or .method(args)
            m = re.match(rf"\.({_NAME_RE})\(([^)]*)\)(.*)", chain, re.DOTALL)
            if m:
                method = m.group(1)
                args_str = m.group(2)
                chain = m.group(3)
                args = (
                    [
                        self._interpret_expression(a.strip(), local_vars)
                        for a in self._comma_split(args_str)
                    ]
                    if args_str.strip()
                    else []
                )
                obj = self._call_method(obj, method, args, local_vars)
                continue

            # .property
            m = re.match(rf"\.({_NAME_RE})(.*)", chain, re.DOTALL)
            if m:
                prop = m.group(1)
                chain = m.group(2)
                obj = self._get_property(obj, prop)
                continue

            # [index]
            m = re.match(r"\[(.+?)\](.*)", chain, re.DOTALL)
            if m:
                idx = self._interpret_expression(m.group(1), local_vars)
                chain = m.group(2)
                if isinstance(obj, dict):
                    obj = obj.get(str(idx), JS_UNDEFINED)
                elif isinstance(obj, (list, str, tuple)):
                    obj = obj[int(idx)]
                continue

            break

        return obj

    def _get_property(self, obj: Any, prop: str) -> Any:
        """Get a property from an object."""
        if prop == "length":
            if isinstance(obj, (str, list)):
                return len(obj)
            return 0

        if isinstance(obj, dict):
            return obj.get(prop, JS_UNDEFINED)

        if isinstance(obj, str):
            if prop == "constructor":
                return {"name": "String"}
            return JS_UNDEFINED

        if isinstance(obj, list):
            if prop == "constructor":
                return {"name": "Array"}
            return JS_UNDEFINED

        return JS_UNDEFINED

    def _call_method(self, obj: Any, method: str, args: list, local_vars: dict) -> Any:
        """Call a method on an object."""
        # String methods
        if isinstance(obj, str):
            if method == "split":
                sep = args[0] if args else ""
                if sep == "":
                    return list(obj)
                return obj.split(sep)
            elif method == "join":
                return (args[0] if args else ",").join(str(x) for x in obj)
            elif method == "slice":
                start = int(args[0]) if args else 0
                end = int(args[1]) if len(args) > 1 else len(obj)
                return obj[start:end]
            elif method == "substring":
                start = int(args[0]) if args else 0
                end = int(args[1]) if len(args) > 1 else len(obj)
                return obj[min(start, end) : max(start, end)]
            elif method == "charAt":
                idx = int(args[0]) if args else 0
                return obj[idx] if 0 <= idx < len(obj) else ""
            elif method == "charCodeAt":
                idx = int(args[0]) if args else 0
                return ord(obj[idx]) if 0 <= idx < len(obj) else float("nan")
            elif method == "indexOf":
                sub = args[0] if args else ""
                start = int(args[1]) if len(args) > 1 else 0
                try:
                    return obj.index(sub, start)
                except ValueError:
                    return -1
            elif method == "replace":
                old = args[0] if args else ""
                new = args[1] if len(args) > 1 else ""
                if isinstance(old, str):
                    return obj.replace(old, str(new), 1)
                return obj
            elif method == "match":
                return re.findall(args[0], obj) if args else None
            elif method == "toLowerCase":
                return obj.lower()
            elif method == "toUpperCase":
                return obj.upper()
            elif method == "trim":
                return obj.strip()
            elif method == "startsWith":
                return obj.startswith(args[0]) if args else False
            elif method == "endsWith":
                return obj.endswith(args[0]) if args else False
            elif method == "includes":
                return (args[0] in obj) if args else False
            elif method == "repeat":
                return obj * int(args[0]) if args else obj
            elif method == "padStart":
                length = int(args[0]) if args else 0
                pad = args[1] if len(args) > 1 else " "
                return obj.rjust(length, pad)

        # Array methods
        elif isinstance(obj, list):
            if method == "push":
                for a in args:
                    obj.append(a)
                return len(obj)
            elif method == "pop":
                return obj.pop() if obj else JS_UNDEFINED
            elif method == "shift":
                return obj.pop(0) if obj else JS_UNDEFINED
            elif method == "unshift":
                for i, a in enumerate(args):
                    obj.insert(i, a)
                return len(obj)
            elif method == "reverse":
                obj.reverse()
                return obj
            elif method == "slice":
                start = int(args[0]) if args else 0
                end = int(args[1]) if len(args) > 1 else len(obj)
                return obj[start:end]
            elif method == "splice":
                start = int(args[0]) if args else 0
                delete_count = int(args[1]) if len(args) > 1 else len(obj) - start
                deleted = obj[start : start + delete_count]
                obj[start : start + delete_count] = args[2:]
                return deleted
            elif method == "indexOf":
                try:
                    return obj.index(args[0])
                except (ValueError, IndexError):
                    return -1
            elif method == "join":
                sep = args[0] if args else ","
                return sep.join(str(x) for x in obj)
            elif method == "forEach":
                callback = args[0] if args else None
                if callable(callback):
                    for i, item in enumerate(obj):
                        callback(item, i, obj)
                return JS_UNDEFINED
            elif method == "map":
                callback = args[0] if args else None
                if callable(callback):
                    return [callback(item, i, obj) for i, item in enumerate(obj)]
                return []
            elif method == "filter":
                callback = args[0] if args else None
                if callable(callback):
                    return [
                        item for i, item in enumerate(obj) if _js_ternary(callback(item, i, obj))
                    ]
                return []
            elif method == "includes":
                return args[0] in obj if args else False
            elif method == "flat":
                depth = int(args[0]) if args else 1
                return self._flat(obj, depth)
            elif method == "concat":
                result = list(obj)
                for a in args:
                    if isinstance(a, list):
                        result.extend(a)
                    else:
                        result.append(a)
                return result
            elif method == "length":
                return len(obj)

        # Object with methods (e.g., extracted JS objects)
        elif isinstance(obj, dict):
            if method in obj:
                method_info = obj[method]
                if isinstance(method_info, tuple) and len(method_info) == 2:
                    arg_names, body = method_info
                    method_vars = dict(local_vars)
                    for i, name in enumerate(arg_names):
                        if i < len(args):
                            method_vars[name] = args[i]
                    return self._interpret_block(body, method_vars)
                elif callable(method_info):
                    return method_info(*args)

        return JS_UNDEFINED

    def _flat(self, arr: list, depth: int) -> list:
        """Flatten a nested array."""
        result = []
        for item in arr:
            if isinstance(item, list) and depth > 0:
                result.extend(self._flat(item, depth - 1))
            else:
                result.append(item)
        return result

    def _try_binary_op(
        self, expr: str, operators: list[str], local_vars: dict
    ) -> tuple[Any, str] | None:
        """Try to parse a binary operation."""
        for op in operators:
            parts = self._split_binary(expr, op)
            if parts:
                left, right = parts
                left_val = self._interpret_expression(left, local_vars)
                right_val = self._interpret_expression(right, local_vars)
                return (self._apply_op(op, left_val, right_val),)
        return None

    def _apply_op(self, op: str, a: Any, b: Any) -> Any:
        """Apply a binary operator."""
        ops = {
            "+": lambda x, y: (
                x + y if isinstance(x, str) or isinstance(y, str) else _to_number(x) + _to_number(y)
            ),
            "-": lambda x, y: _to_number(x) - _to_number(y),
            "*": lambda x, y: _to_number(x) * _to_number(y),
            "/": lambda x, y: _to_number(x) / _to_number(y) if _to_number(y) else float("inf"),
            "%": lambda x, y: _to_number(x) % _to_number(y) if _to_number(y) else float("nan"),
            "|": lambda x, y: int(_to_number(x)) | int(_to_number(y)),
            "^": lambda x, y: int(_to_number(x)) ^ int(_to_number(y)),
            "&": lambda x, y: int(_to_number(x)) & int(_to_number(y)),
            ">>": lambda x, y: int(_to_number(x)) >> int(_to_number(y)),
            "<<": lambda x, y: int(_to_number(x)) << int(_to_number(y)),
            ">>>": lambda x, y: (int(_to_number(x)) % (1 << 32)) >> int(_to_number(y)),
            "===": lambda x, y: x is y if type(x) is type(y) else x == y,
            "!==": lambda x, y: not (x is y if type(x) is type(y) else x == y),
            "==": lambda x, y: x == y,
            "!=": lambda x, y: x != y,
            "<": lambda x, y: _to_number(x) < _to_number(y),
            ">": lambda x, y: _to_number(x) > _to_number(y),
            "<=": lambda x, y: _to_number(x) <= _to_number(y),
            ">=": lambda x, y: _to_number(x) >= _to_number(y),
            "||": lambda x, y: x if _js_ternary(x) else y,
            "&&": lambda x, y: y if _js_ternary(x) else x,
        }

        if op in ops:
            try:
                return ops[op](a, b)
            except Exception:
                return JS_UNDEFINED
        return JS_UNDEFINED

    def _split_binary(self, expr: str, op: str) -> tuple[str, str] | None:
        """Split expression at a binary operator, respecting nesting."""
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        in_string = None
        escape = False
        op_len = len(op)

        # Search from right to left for left-associative operators
        i = len(expr) - 1
        while i >= op_len:
            c = expr[i]

            if escape:
                escape = False
                i -= 1
                continue

            if c == "\\" and in_string:
                escape = True
                i -= 1
                continue

            if c in ('"', "'") and in_string is None:
                in_string = c
                i -= 1
                continue
            elif c == in_string:
                in_string = None
                i -= 1
                continue

            if in_string:
                i -= 1
                continue

            if c == ")":
                depth_paren += 1
            elif c == "(":
                depth_paren -= 1
            elif c == "]":
                depth_bracket += 1
            elif c == "[":
                depth_bracket -= 1
            elif c == "}":
                depth_brace += 1
            elif c == "{":
                depth_brace -= 1

            if depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                if expr[i - op_len + 1 : i + 1] == op:
                    # Make sure it's not part of a longer operator
                    before = expr[i - op_len] if i - op_len >= 0 else ""
                    after = expr[i + 1] if i + 1 < len(expr) else ""

                    # Avoid matching === when looking for ==, etc.
                    if op == "==" and (before == "!" or before == "=" or after == "="):
                        i -= 1
                        continue
                    if op == "!=" and (before == "!" or after == "="):
                        i -= 1
                        continue
                    if op == "<" and after == "=":
                        i -= 1
                        continue
                    if op == ">" and (after == "=" or before == ">" or before == "<"):
                        i -= 1
                        continue

                    left = expr[: i - op_len + 1].strip()
                    right = expr[i + 1 :].strip()
                    if left and right:
                        return left, right

            i -= 1

        return None

    def _split_ternary(self, expr: str) -> tuple[str, str, str] | None:
        """Split a ternary expression into condition, if_true, if_false."""
        depth = 0
        in_string = None
        question_pos = -1
        colon_pos = -1

        for i, c in enumerate(expr):
            if c in ('"', "'") and in_string is None:
                in_string = c
            elif c == in_string:
                in_string = None
            elif in_string:
                continue
            elif c in ("(", "[", "{"):
                depth += 1
            elif c in (")", "]", "}"):
                depth -= 1
            elif c == "?" and depth == 0 and question_pos < 0:
                question_pos = i
            elif c == ":" and depth == 0 and question_pos >= 0:
                colon_pos = i
                break

        if question_pos > 0 and colon_pos > question_pos:
            return (
                expr[:question_pos].strip(),
                expr[question_pos + 1 : colon_pos].strip(),
                expr[colon_pos + 1 :].strip(),
            )
        return None

    def _comma_split(self, expr: str) -> list[str]:
        """Split at commas, respecting nesting."""
        parts = []
        current = []
        depth = 0
        in_string = None

        for c in expr:
            if c in ('"', "'") and in_string is None:
                in_string = c
            elif c == in_string:
                in_string = None
            elif not in_string:
                if c in ("(", "[", "{"):
                    depth += 1
                elif c in (")", "]", "}"):
                    depth -= 1
                elif c == "," and depth == 0:
                    parts.append("".join(current))
                    current = []
                    continue
            current.append(c)

        if current:
            parts.append("".join(current))

        return parts

    def _find_matching_paren(self, expr: str) -> tuple[str, str]:
        """Find content inside parentheses and return (inner, rest)."""
        if not expr or expr[0] != "(":
            return expr, ""

        depth = 0
        in_string = None

        for i, c in enumerate(expr):
            if c in ('"', "'") and in_string is None:
                in_string = c
            elif c == in_string:
                in_string = None
            elif not in_string:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        return expr[1:i], expr[i + 1 :]

        return expr[1:], ""

    def _handle_if(self, stmt: str, local_vars: dict) -> tuple[Any, bool]:
        """Handle if/else if/else statement."""
        m = re.match(r"if\s*\(", stmt)
        if not m:
            return None, False

        cond_start = stmt.find("(")
        cond_inner, rest = self._find_matching_paren(stmt[cond_start:])
        rest = rest.strip()

        # Extract if body
        if rest.startswith("{"):
            body = self._find_matching_brace(rest, 0)
            rest = rest[len(body) + 2 :].strip()
        else:
            body = rest.split(";")[0]
            rest = rest[len(body) + 1 :].strip() if ";" in rest else ""

        # Check for else
        else_body = None
        if rest.startswith("else"):
            rest = rest[4:].strip()
            if rest.startswith("if"):
                else_body = rest
            elif rest.startswith("{"):
                else_body = self._find_matching_brace(rest, 0)
            else:
                else_body = rest.split(";")[0]

        cond_val = self._interpret_expression(cond_inner, local_vars)

        if _js_ternary(cond_val):
            return self._interpret_block(body, local_vars), False
        elif else_body:
            if else_body.startswith("if"):
                return self._handle_if(else_body, local_vars)
            return self._interpret_block(else_body, local_vars), False

        return None, False

    def _handle_for(self, stmt: str, local_vars: dict) -> tuple[Any, bool]:
        """Handle for loop."""
        m = re.match(r"for\s*\(", stmt)
        if not m:
            return None, False

        paren_start = stmt.find("(")
        constructor, rest = self._find_matching_paren(stmt[paren_start:])
        rest = rest.strip()

        if rest.startswith("{"):
            body = self._find_matching_brace(rest, 0)
        else:
            body = rest

        parts = constructor.split(";")
        if len(parts) != 3:
            return None, False

        init, cond, increment = parts

        # Execute init
        if init.strip():
            self._interpret_statement(init.strip(), local_vars)

        # Loop
        max_iterations = 100000  # Safety limit
        for _ in range(max_iterations):
            if cond.strip():
                cond_val = self._interpret_expression(cond.strip(), local_vars)
                if not _js_ternary(cond_val):
                    break

            try:
                self._interpret_block(body, local_vars)
            except JSBreak:
                break
            except JSContinue:
                pass

            if increment.strip():
                self._interpret_expression(increment.strip(), local_vars)

        return None, False

    def _handle_while(self, stmt: str, local_vars: dict) -> tuple[Any, bool]:
        """Handle while loop."""
        m = re.match(r"while\s*\(", stmt)
        if not m:
            return None, False

        paren_start = stmt.find("(")
        cond, rest = self._find_matching_paren(stmt[paren_start:])
        rest = rest.strip()

        if rest.startswith("{"):
            body = self._find_matching_brace(rest, 0)
        else:
            body = rest

        max_iterations = 100000
        for _ in range(max_iterations):
            cond_val = self._interpret_expression(cond, local_vars)
            if not _js_ternary(cond_val):
                break

            try:
                self._interpret_block(body, local_vars)
            except JSBreak:
                break
            except JSContinue:
                pass

        return None, False

    def _handle_try(self, stmt: str, local_vars: dict) -> tuple[Any, bool]:
        """Handle try/catch."""
        m = re.match(r"try\s*\{", stmt)
        if not m:
            return None, False

        try_body = self._find_matching_brace(stmt, stmt.find("{"))
        rest = stmt[stmt.find("}", stmt.find("{") + len(try_body)) + 1 :].strip()

        try:
            result = self._interpret_block(try_body, local_vars)
            return result, False
        except Exception as e:
            # Look for catch
            m2 = re.match(r"catch\s*(?:\(\s*(\w+)\s*\))?\s*\{", rest)
            if m2:
                catch_body = self._find_matching_brace(rest, rest.find("{"))
                catch_vars = dict(local_vars)
                if m2.group(1):
                    catch_vars[m2.group(1)] = str(e)
                result = self._interpret_block(catch_body, catch_vars)
                return result, False

        return None, False

    def _handle_postfix(self, val: Any, rest: str, local_vars: dict) -> Any:
        """Handle postfix operations on a value (method calls, property access)."""
        return self._resolve_chain(val, rest, local_vars)
