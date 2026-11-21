"""Reviewed document semantics for the PostgreSQL repository.

Unknown operators fail before data is inspected. BSON missing/null, numeric and
multikey behavior is retained explicitly rather than delegated to JSON equality.
"""
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import cmp_to_key
import math
import numbers
import re

from bson import Binary, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp


class UnsupportedDocumentOperation(ValueError):
    pass


class _Missing:
    def __repr__(self): return "MISSING"
    def __bool__(self): return False
    def __copy__(self): return self
    def __deepcopy__(self, memo): return self


MISSING = _Missing()


def _error(message="Unsupported document operation"):
    raise UnsupportedDocumentOperation(message)


def _null(value):
    return value is MISSING or value is None


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (numbers.Number, Decimal, Decimal128))


def _decimal(value):
    return value.to_decimal() if isinstance(value, Decimal128) else value if isinstance(value, Decimal) else Decimal(str(value))


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _millis(value):
    return math.floor((_utc(value)-datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()*1000)


def _clock(value=None):
    value = value if value is not None else datetime.now(timezone.utc)
    return _utc(value).replace(microsecond=value.microsecond//1000*1000)


def bson_type(value):
    if value is MISSING: return "missing"
    if value is None: return "null"
    if isinstance(value, bool): return "bool"
    if isinstance(value, Int64): return "long"
    if isinstance(value, int): return "int" if -(2**31) <= value < 2**31 else "long"
    if isinstance(value, float): return "double"
    if isinstance(value, (Decimal, Decimal128)): return "decimal"
    if isinstance(value, str): return "string"
    if isinstance(value, Mapping): return "object"
    if isinstance(value, (list, tuple)): return "array"
    if isinstance(value, (bytes, bytearray, Binary)): return "binData"
    if isinstance(value, ObjectId): return "objectId"
    if isinstance(value, datetime): return "date"
    if isinstance(value, Timestamp): return "timestamp"
    if isinstance(value, (Regex, re.Pattern)): return "regex"
    if isinstance(value, MinKey): return "minKey"
    if isinstance(value, MaxKey): return "maxKey"
    _error("Unsupported BSON value type")


def bson_equal(left, right):
    if left is MISSING or right is MISSING: return left is right
    if _number(left) and _number(right):
        a, b = _decimal(left), _decimal(right)
        return (a.is_nan() and b.is_nan()) or a == b
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return list(left) == list(right) and all(bson_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(bson_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, datetime) and isinstance(right, datetime): return _millis(left) == _millis(right)
    if isinstance(left, (bytes, bytearray)) and isinstance(right, (bytes, bytearray)):
        return getattr(left, "subtype", 0) == getattr(right, "subtype", 0) and bytes(left) == bytes(right)
    return bson_type(left) == bson_type(right) and left == right


_RANKS = {"minKey": -2, "missing": -1, "null": 0, "int": 1, "long": 1, "double": 1, "decimal": 1,
          "string": 2, "object": 3, "array": 4, "binData": 5, "objectId": 6, "bool": 7,
          "date": 8, "timestamp": 9, "regex": 10, "maxKey": 100}


def compare_values(left, right):
    if bson_equal(left, right): return 0
    ra, rb = _RANKS[bson_type(left)], _RANKS[bson_type(right)]
    if ra != rb: return -1 if ra < rb else 1
    if _number(left):
        a, b = _decimal(left), _decimal(right)
        if a.is_nan(): return -1
        if b.is_nan(): return 1
        return -1 if a < b else 1
    if isinstance(left, Mapping):
        for (ka, va), (kb, vb) in zip(left.items(), right.items()):
            ra, rb = _RANKS[bson_type(va)], _RANKS[bson_type(vb)]
            if ra != rb: return -1 if ra < rb else 1
            if ka != kb: return -1 if ka < kb else 1
            result = compare_values(va, vb)
            if result: return result
        return -1 if len(left) < len(right) else 1
    if isinstance(left, (list, tuple)):
        for a, b in zip(left, right):
            result = compare_values(a, b)
            if result: return result
        return -1 if len(left) < len(right) else 1
    if isinstance(left, datetime): left, right = _millis(left), _millis(right)
    elif isinstance(left, ObjectId): left, right = left.binary, right.binary
    elif isinstance(left, Timestamp): left, right = (left.time, left.inc), (right.time, right.inc)
    elif isinstance(left, (bytes, bytearray)):
        left, right = (len(left), getattr(left, "subtype", 0), bytes(left)), (len(right), getattr(right, "subtype", 0), bytes(right))
    elif isinstance(left, (Regex, re.Pattern)): left, right = (left.pattern, left.flags), (right.pattern, right.flags)
    return -1 if left < right else 1


def get_path(doc, dotted, default=MISSING):
    if not isinstance(dotted, str): _error("Document path must be text")
    def read(value, parts):
        if not parts: return value
        part, rest = parts[0], parts[1:]
        if isinstance(value, Mapping): return read(value[part], rest) if part in value else MISSING
        if isinstance(value, (list, tuple)):
            if part.isdigit(): return read(value[int(part)], rest) if int(part) < len(value) else MISSING
            found = [read(item, parts) for item in value]
            return [item for item in found if item is not MISSING]
        return MISSING
    value = read(doc, dotted.split(".") if dotted else [])
    return default if value is MISSING else value


def path_values(doc, dotted):
    if not isinstance(dotted, str): _error("Document path must be text")
    def read(value, parts):
        if not parts: return [value]
        part, rest = parts[0], parts[1:]
        if isinstance(value, Mapping): return read(value[part], rest) if part in value else []
        if isinstance(value, (list, tuple)):
            if part.isdigit(): return read(value[int(part)], rest) if int(part) < len(value) else []
            return [result for item in value for result in read(item, parts)]
        return []
    return read(doc, dotted.split(".") if dotted else [])


def _set_path(doc, path, value):
    parts, current = path.split("."), doc
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict): current[part] = {}
        current = current[part]
    if value is MISSING: current.pop(parts[-1], None)
    else: current[parts[-1]] = deepcopy(value)


def _unset_path(doc, path):
    def remove(value, parts):
        if isinstance(value, list):
            for item in value: remove(item, parts)
        elif isinstance(value, dict):
            if len(parts) == 1: value.pop(parts[0], None)
            elif parts[0] in value: remove(value[parts[0]], parts[1:])
    remove(doc, path.split("."))


def _truth(value):
    return not (_null(value) or value is False or (_number(value) and value == 0))


def _compatible_pattern(pattern, flags):
    """Keep the app's PCRE character classes/folding without native libraries."""
    if re.search(r"\(\?[aiLmsux-]+[:)]", pattern):
        _error("Inline regex flags are outside the reviewed vocabulary")
    classes = {"d": "0-9", "w": "A-Za-z0-9_", "s": r" \t\r\n\f\v"}
    output, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index+1 < len(pattern):
            escaped = pattern[index+1]
            if escaped.lower() in classes:
                output.append("(?a:\\"+escaped+")")
            else: output.append(pattern[index:index+2])
            index += 2
            continue
        if char == "[":
            end = index+1
            while end < len(pattern):
                if pattern[end] == "\\": end += 2; continue
                if pattern[end] == "]" and end > index+1: break
                end += 1
            fragment = pattern[index:end+1]
            for key, replacement in classes.items():
                fragment = fragment.replace("\\"+key, replacement)
            if any("\\"+key.upper() in fragment for key in classes):
                _error("Complement escapes inside regex classes are unsupported")
            if flags & re.I:
                base = re.compile(fragment, flags & ~re.I)
                negated, desired = fragment.startswith("[^"), []
                for candidate, equivalents in (("i", "iI"), ("I", "iI"), ("İ", "İ"), ("ı", "ı")):
                    tests = [base.fullmatch(value) is not None for value in equivalents]
                    if (all(tests) if negated else any(tests)): desired.append(candidate)
                override = "(?-i:["+"".join(desired)+"])" if desired else "(?!)"
                fragment = "(?:(?![iIİı])"+fragment+"|"+override+")"
            output.append(fragment); index = end+1
            continue
        if flags & re.I and char in "iI": output.append("(?ai:"+char+")")
        elif flags & re.I and char in "İı": output.append("(?-i:"+char+")")
        else: output.append(char)
        index += 1
    return "".join(output)


def _regex(pattern, options=""):
    flags = 0
    if isinstance(pattern, (Regex, re.Pattern)): flags, pattern = pattern.flags, pattern.pattern
    if not isinstance(pattern, str) or not isinstance(options, str): _error("Invalid regular expression")
    for option in options:
        if option not in "imsxu": _error("Unsupported regular-expression option")
        flags |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X, "u": re.U}[option]
    try: return re.compile(_compatible_pattern(pattern, flags), flags)
    except (re.error, ValueError): _error("Invalid regular expression")


def _equal_query(candidate, expected, *, expand_array=True):
    if expected is None and _null(candidate): return True
    if isinstance(expected, (Regex, re.Pattern)):
        return isinstance(candidate, str) and _regex(expected).search(candidate) is not None
    if bson_equal(candidate, expected): return True
    return expand_array and isinstance(candidate, (list, tuple)) and any(
        bson_equal(item, expected) or isinstance(expected, (Regex, re.Pattern)) and isinstance(item, str)
        and _regex(expected).search(item) is not None for item in candidate)


_QUERY_OPS = {"$eq", "$ne", "$in", "$nin", "$gt", "$gte", "$lt", "$lte", "$exists", "$type", "$regex", "$options",
              "$not", "$elemMatch", "$size", "$all"}
_TYPE_CODES = {1: "double", 2: "string", 3: "object", 4: "array", 5: "binData", 7: "objectId", 8: "bool", 9: "date",
               10: "null", 11: "regex", 16: "int", 17: "timestamp", 18: "long", 19: "decimal", -1: "minKey", 127: "maxKey"}
_EXPRESSION_OPS = {"$literal", "$ifNull", "$cond", "$switch", "$let", "$map", "$filter", "$reduce", "$type", "$isArray", "$isNumber",
 "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$and", "$or", "$not", "$in", "$setEquals", "$setIsSubset", "$setUnion",
 "$anyElementTrue", "$allElementsTrue", "$arrayElemAt", "$indexOfArray", "$size", "$slice", "$concatArrays", "$objectToArray",
 "$arrayToObject", "$getField", "$mergeObjects", "$concat", "$trim", "$strLenCP", "$substrBytes", "$regexMatch", "$regexFind",
 "$convert", "$toInt", "$toLong", "$toString", "$toDouble", "$toDate", "$toBool", "$add", "$subtract", "$multiply", "$divide",
 "$mod", "$floor", "$ceil", "$sqrt", "$abs", "$round", "$sum", "$avg", "$min", "$max", "$first", "$last", "$dateToString",
 "$pow", "$sin", "$cos", "$asin", "$degreesToRadians"}
_UNARY_OPS = {"$type", "$isArray", "$isNumber", "$size", "$objectToArray", "$arrayToObject", "$strLenCP",
              "$toInt", "$toLong", "$toString", "$toDouble", "$toDate", "$toBool", "$floor", "$ceil", "$sqrt", "$abs", "$first", "$last",
              "$sin", "$cos", "$asin", "$degreesToRadians"}
_BINARY_OPS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$setIsSubset", "$arrayElemAt",
               "$subtract", "$divide", "$mod", "$pow"}


def validate_expression(expr):
    if isinstance(expr, Mapping):
        dollar = [key for key in expr if isinstance(key, str) and key.startswith("$")]
        if dollar:
            if len(expr) != 1 or dollar[0] not in _EXPRESSION_OPS: _error("Unsupported expression operator")
            if dollar[0] == "$literal": return
            op, raw = dollar[0], expr[dollar[0]]
            if op in _UNARY_OPS and isinstance(raw, (list, tuple)) and len(raw) != 1: _error("Unary expression arity")
            if op in _BINARY_OPS and (not isinstance(raw, (list, tuple)) or len(raw) != 2): _error("Binary expression arity")
            if op in ("$not", "$anyElementTrue", "$allElementsTrue") and (not isinstance(raw, (list, tuple)) or len(raw) != 1): _error("Unary expression arity")
            if op == "$ifNull" and (not isinstance(raw, (list, tuple)) or len(raw) < 2): _error("ifNull expression arity")
            if op == "$cond" and not (isinstance(raw, Mapping) and set(raw) == {"if", "then", "else"} or isinstance(raw, (list, tuple)) and len(raw) == 3): _error("Conditional expression arity")
            shapes = {
                "$let": ({"vars", "in"}, {"vars", "in"}), "$map": ({"input", "in"}, {"input", "in", "as"}),
                "$filter": ({"input", "cond"}, {"input", "cond", "as", "limit"}),
                "$reduce": ({"input", "initialValue", "in"}, {"input", "initialValue", "in"}),
                "$convert": ({"input", "to"}, {"input", "to", "onError", "onNull"}),
                "$regexFind": ({"input", "regex"}, {"input", "regex", "options"}),
                "$regexMatch": ({"input", "regex"}, {"input", "regex", "options"}),
                "$trim": ({"input"}, {"input", "chars"}),
                "$dateToString": ({"date"}, {"date", "format", "timezone", "onNull"}),
            }
            if op in shapes:
                required, allowed = shapes[op]
                if not isinstance(raw, Mapping) or not required <= set(raw) or set(raw)-allowed: _error("Invalid expression fields")
            if op == "$let" and not isinstance(raw["vars"], Mapping): _error("Let variables require object")
            if op == "$switch":
                if not isinstance(raw, Mapping) or set(raw)-{"branches", "default"} or not isinstance(raw.get("branches"), (list, tuple)) or not raw["branches"]: _error("Invalid switch branches")
                if any(not isinstance(branch, Mapping) or set(branch) != {"case", "then"} for branch in raw["branches"]): _error("Invalid switch branch")
        for value in expr.values(): validate_expression(value)
    elif isinstance(expr, (list, tuple)):
        for value in expr: validate_expression(value)


def _validate_condition(condition):
    if not isinstance(condition, Mapping) or not any(str(k).startswith("$") for k in condition): return
    if any(key not in _QUERY_OPS for key in condition): _error("Unsupported query operator")
    for op, value in condition.items():
        if op in ("$in", "$nin", "$all") and not isinstance(value, (list, tuple)): _error("Membership query requires array")
        if op == "$not":
            if not isinstance(value, (Mapping, Regex, re.Pattern)): _error("$not requires condition or regex")
            _validate_condition(value)
        if op == "$size" and (not isinstance(value, int) or isinstance(value, bool) or value < 0): _error("Invalid query size")
        if op == "$type":
            types = value if isinstance(value, (list, tuple)) else [value]
            if not types or any(_TYPE_CODES.get(item, item) not in set(_RANKS) | {"number"} for item in types):
                _error("Invalid query BSON type")
        if op == "$options" and "$regex" not in condition: _error("$options requires $regex")
        if op == "$regex": _regex(value, condition.get("$options", ""))
        if op == "$elemMatch":
            if not isinstance(value, Mapping): _error("$elemMatch requires object")
            if all(k in _QUERY_OPS for k in value): _validate_condition(value)
            else: validate_query(value)


def validate_query(query):
    if not isinstance(query, Mapping): _error("Query must be an object")
    for key, value in query.items():
        if key in ("$and", "$or", "$nor"):
            if not isinstance(value, (list, tuple)) or not value: _error("Logical query requires nonempty array")
            for part in value: validate_query(part)
        elif key == "$expr": validate_expression(value)
        elif not isinstance(key, str) or key.startswith("$"): _error("Unsupported top-level query operator")
        else: _validate_condition(value)


def _condition(values, condition, now, *, expand_arrays=True):
    values = values if values else [MISSING]
    if not isinstance(condition, Mapping) or not any(str(k).startswith("$") for k in condition):
        return any(_equal_query(value, condition, expand_array=expand_arrays) for value in values)
    for op, operand in condition.items():
        expanded = [item for value in values for item in ([value, *value] if expand_arrays and isinstance(value, (list, tuple)) else [value])]
        if op == "$eq": passed = any(_equal_query(v, operand, expand_array=expand_arrays) for v in values)
        elif op == "$ne": passed = not any(_equal_query(v, operand, expand_array=expand_arrays) for v in values)
        elif op in ("$in", "$nin"):
            passed = any(_equal_query(v, item, expand_array=expand_arrays) for v in values for item in operand)
            if op == "$nin": passed = not passed
        elif op in ("$gt", "$gte", "$lt", "$lte"):
            def ordered(v):
                if _null(v) and operand is None: return op in ("$gte", "$lte")
                if (_number(v) and _number(operand)) or bson_type(v) == bson_type(operand):
                    c = compare_values(v, operand)
                    return {"$gt": c > 0, "$gte": c >= 0, "$lt": c < 0, "$lte": c <= 0}[op]
                return False
            passed = any(ordered(v) for v in expanded)
        elif op == "$exists": passed = any(v is not MISSING for v in values) == bool(operand)
        elif op == "$type":
            wanted = {_TYPE_CODES.get(t, t) for t in (operand if isinstance(operand, (list, tuple)) else [operand])}
            passed = any(bson_type(v) in wanted or ("number" in wanted and _number(v)) for v in expanded)
        elif op == "$regex":
            compiled = _regex(operand, condition.get("$options", ""))
            passed = any(isinstance(v, str) and compiled.search(v) is not None for v in expanded)
        elif op == "$options": passed = True
        elif op == "$not": passed = not _condition(values, operand, now, expand_arrays=expand_arrays)
        elif op == "$size":
            if not isinstance(operand, int) or isinstance(operand, bool) or operand < 0: _error("Invalid $size")
            passed = any(isinstance(v, (list, tuple)) and len(v) == operand for v in values)
        elif op == "$all":
            passed = bool(operand) and any(isinstance(v, (list, tuple)) and all(_condition([v], item, now) if isinstance(item, dict) and "$elemMatch" in item else any(_equal_query(x, item) for x in v) for item in operand) for v in values)
        elif op == "$elemMatch":
            mode = all(key in _QUERY_OPS for key in operand)
            passed = any(isinstance(v, (list, tuple)) and any(_condition([item], operand, now, expand_arrays=False) if mode else isinstance(item, Mapping) and matches(item, operand, now=now) for item in v) for v in values)
        else: _error("Unsupported query operator")
        if not passed: return False
    return True


def matches(doc, query, *, now=None):
    return compile_query(query, now=now)(doc)


def _membership_index(values, *, query):
    buckets, patterns = {}, []
    for value in values:
        if query and isinstance(value, (Regex, re.Pattern)): patterns.append(_regex(value))
        else: buckets.setdefault(bson_hash_key(value), []).append(value)
    return buckets, patterns


def _membership_contains(value, index, *, query=False, expand=False):
    buckets, patterns = index
    values = [value, *value] if expand and isinstance(value, (list, tuple)) else [value]
    for item in values:
        key = bson_hash_key(None if query and item is MISSING else item)
        for expected in buckets.get(key, ()):
            equal = _equal_query(item, expected, expand_array=False) if query else bson_equal(item, expected)
            if equal: return True
        if query and isinstance(item, str) and any(pattern.search(item) is not None for pattern in patterns): return True
    return False


def _compile_condition(condition, now, *, expand_arrays=True):
    if not isinstance(condition, Mapping) or not any(str(key).startswith("$") for key in condition):
        return lambda values: any(_equal_query(value, condition, expand_array=expand_arrays) for value in (values or [MISSING]))
    parts = []
    for op, operand in condition.items():
        if op in ("$in", "$nin"):
            index = _membership_index(operand, query=True)
            def contains(values, index=index, invert=op == "$nin"):
                found = any(_membership_contains(value, index, query=True, expand=expand_arrays) for value in (values or [MISSING]))
                return not found if invert else found
            parts.append(contains)
        elif op == "$not":
            child = _compile_condition(operand, now, expand_arrays=expand_arrays)
            parts.append(lambda values, child=child: not child(values))
        elif op == "$elemMatch":
            mode = all(key in _QUERY_OPS for key in operand)
            child = _compile_condition(operand, now, expand_arrays=False) if mode else _compile_query_node(operand, now, {}, {})
            def elements(values, child=child, mode=mode):
                return any(isinstance(value, (list, tuple)) and any(child([item]) if mode else
                    isinstance(item, Mapping) and child(item) for item in value) for value in values)
            parts.append(elements)
        elif op == "$regex":
            compiled = _regex(operand, condition.get("$options", ""))
            def regex(values, compiled=compiled):
                expanded = [item for value in values for item in ([value, *value] if expand_arrays and isinstance(value, (list, tuple)) else [value])]
                return any(isinstance(value, str) and compiled.search(value) is not None for value in expanded)
            parts.append(regex)
        elif op != "$options":
            parts.append(lambda values, op=op, operand=operand: _condition(values, {op: operand}, now, expand_arrays=expand_arrays))
    return lambda values: all(part(values) for part in parts)


def _compile_query_node(query, now, variables, context):
    parts = []
    for field, condition in query.items():
        if field in ("$and", "$or", "$nor"):
            children = [_compile_query_node(part, now, variables, context) for part in condition]
            if field == "$and": parts.append(lambda doc, children=children: all(child(doc) for child in children))
            elif field == "$or": parts.append(lambda doc, children=children: any(child(doc) for child in children))
            else: parts.append(lambda doc, children=children: not any(child(doc) for child in children))
        elif field == "$expr":
            parts.append(lambda doc, expr=condition: _truth(_evaluate(expr, doc, variables, now, context)))
        else:
            predicate = _compile_condition(condition, now)
            parts.append(lambda doc, field=field, predicate=predicate: predicate(path_values(doc, field)))
    return lambda doc: all(part(doc) for part in parts)


def compile_query(query, *, now=None, variables=None):
    """Compile once per repository operation, never cache caller data globally."""
    validate_query(query)
    query = deepcopy(query)
    return _compile_query_node(query, _clock(now), variables or {}, {})


def match_documents(rows, query, *, now=None, variables=None):
    predicate = compile_query(query, now=now, variables=variables)
    return [doc for doc in rows if predicate(doc)]


def _matches_variables(doc, query, variables, now):
    for field, condition in query.items():
        if field in ("$and", "$or", "$nor"):
            parts = [_matches_variables(doc, item, variables, now) for item in condition]
            passed = all(parts) if field == "$and" else not any(parts) if field == "$nor" else any(parts)
        elif field == "$expr": passed = _truth(evaluate(condition, doc, variables=variables, now=now))
        else: passed = _condition(path_values(doc, field), condition, now)
        if not passed: return False
    return True


def bson_hash_key(value):
    """Hashable BSON equality key; buckets still check bson_equal defensively."""
    kind = bson_type(value)
    if _number(value):
        number = _decimal(value)
        return ("number", "NaN" if number.is_nan() else "0" if number.is_zero() else str(number.normalize()))
    if isinstance(value, Mapping): return ("object", tuple((key, bson_hash_key(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)): return ("array", tuple(bson_hash_key(item) for item in value))
    if isinstance(value, datetime): return ("date", _millis(value))
    if isinstance(value, ObjectId): return ("objectId", value.binary)
    if isinstance(value, Timestamp): return ("timestamp", value.time, value.inc)
    if isinstance(value, (bytes, bytearray)): return ("binData", getattr(value, "subtype", 0), bytes(value))
    if isinstance(value, (Regex, re.Pattern)): return ("regex", value.pattern, value.flags)
    if kind in ("missing", "null", "minKey", "maxKey"): return (kind,)
    return (kind, value)


def _unique(values):
    result, buckets = [], {}
    for value in values:
        bucket = buckets.setdefault(bson_hash_key(value), [])
        if not any(bson_equal(value, item) for item in bucket):
            result.append(value); bucket.append(value)
    return result


def _convert(value, target):
    if _null(value): return None
    if target in ("int", "long"):
        if isinstance(value, datetime): result = _millis(value)
        elif isinstance(value, bool): result = int(value)
        elif _number(value): result = int(_decimal(value))
        elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value): result = int(value)
        else: raise ValueError("Invalid integer conversion")
        bits = 32 if target == "int" else 64
        if not -(2**(bits-1)) <= result < 2**(bits-1): raise ValueError("Integer overflow")
        return result if target == "int" else Int64(result)
    if target == "string":
        if isinstance(value, str): return value
        if isinstance(value, bool): return "true" if value else "false"
        if isinstance(value, datetime): return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if isinstance(value, ObjectId): return str(value)
        if _number(value):
            if isinstance(value, float) and value.is_integer(): return str(int(value))
            return str(value.to_decimal()) if isinstance(value, Decimal128) else str(value)
        raise ValueError("Invalid string conversion")
    if target == "date":
        if isinstance(value, datetime): result = _utc(value)
        elif _number(value): result = datetime(1970, 1, 1, tzinfo=timezone.utc)+timedelta(milliseconds=float(value))
        elif isinstance(value, str): result = _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        else: raise ValueError("Invalid date conversion")
        return result.replace(microsecond=result.microsecond//1000*1000)
    if target == "double":
        if isinstance(value, (str, bool)) or _number(value): return float(value.to_decimal() if isinstance(value, Decimal128) else value)
        raise ValueError("Invalid double conversion")
    if target == "bool": return _truth(value)
    if target == "objectId" and isinstance(value, str): return ObjectId(value)
    _error("Unsupported conversion target")


def evaluate(expr, doc, *, variables=None, now=None):
    validate_expression(expr)
    return _evaluate(expr, doc, variables or {}, _clock(now), {})


def _constant_expression(value):
    if isinstance(value, str): return not value.startswith("$")
    if isinstance(value, Mapping):
        if set(value) == {"$literal"}: return True
        if set(value) == {"$getField"}:
            # Shorthand and an omitted input read the current document without
            # a "$" reference. They must never populate a cross-row index.
            spec = value["$getField"]
            if not isinstance(spec, Mapping) or "input" not in spec: return False
        return all(_constant_expression(item) for item in value.values())
    return not isinstance(value, (list, tuple)) or all(_constant_expression(item) for item in value)


def _expression_membership(expression, array, context):
    # Retain an object reference with every identity-keyed cache entry. The
    # operation owns this bounded cache, so GC/id reuse cannot mix two arrays.
    marker = ("membership", id(expression))
    entry = context.get(marker)
    if entry is not None and entry[0] is expression: return entry[1]
    index = _membership_index(array, query=False)
    if len(context) < 128: context[marker] = (expression, index)
    return index


def _evaluate(expr, doc, variables, now, context=None):
    context = {} if context is None else context
    def ev(value, scope=None): return _evaluate(value, doc, variables if scope is None else scope, now, context)
    def args(value, count=None):
        if not isinstance(value, (list, tuple)) or (count is not None and len(value) != count): _error("Invalid expression arity")
        return [ev(item) for item in value]
    if isinstance(expr, str):
        if expr.startswith("$$"):
            name, _, path = expr[2:].partition(".")
            if name == "NOW": value = now
            elif name in ("ROOT", "CURRENT"): value = doc
            elif name == "REMOVE": value = MISSING
            elif name in variables: value = variables[name]
            else: _error("Unknown expression variable")
            return get_path(value, path) if path else value
        return get_path(doc, expr[1:]) if expr.startswith("$") else expr
    if isinstance(expr, (list, tuple)): return [None if (v := ev(item)) is MISSING else v for item in expr]
    if not isinstance(expr, Mapping): return expr
    if not expr or not any(str(key).startswith("$") for key in expr):
        return {key: value for key, item in expr.items() if (value := ev(item)) is not MISSING}
    op, raw = next(iter(expr.items()))
    if op in _UNARY_OPS and isinstance(raw, (list, tuple)): raw = raw[0]
    if op == "$literal": return deepcopy(raw)
    if op == "$isNumber": return _number(ev(raw))
    if op == "$ifNull":
        if not isinstance(raw, (list, tuple)) or len(raw) < 2: _error("$ifNull arity")
        for item in raw[:-1]:
            value = ev(item)
            if not _null(value): return value
        return ev(raw[-1])
    if op == "$cond":
        if isinstance(raw, Mapping): condition, yes, no = raw["if"], raw["then"], raw["else"]
        elif isinstance(raw, (list, tuple)) and len(raw) == 3: condition, yes, no = raw
        else: _error("$cond arity")
        return ev(yes if _truth(ev(condition)) else no)
    if op == "$switch":
        for branch in raw["branches"]:
            if _truth(ev(branch["case"])): return ev(branch["then"])
        if "default" not in raw: _error("$switch has no matching branch")
        return ev(raw["default"])
    if op == "$let": return ev(raw["in"], {**variables, **{name: ev(value) for name, value in raw["vars"].items()}})
    if op in ("$map", "$filter", "$reduce"):
        source = ev(raw["input"])
        if _null(source): return None
        if not isinstance(source, (list, tuple)): _error("Array expression requires array")
        if op == "$reduce":
            value = ev(raw["initialValue"])
            for item in source: value = ev(raw["in"], {**variables, "value": value, "this": item})
            return value
        result = []
        for item in source:
            scope = {**variables, raw.get("as", "this"): item}
            if op == "$map":
                value = ev(raw["in"], scope); result.append(None if value is MISSING else value)
            elif _truth(ev(raw["cond"], scope)): result.append(deepcopy(item))
        if op == "$filter" and "limit" in raw:
            limit = ev(raw["limit"])
            if limit is not None:
                if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1: _error("Invalid filter limit")
                result = result[:limit]
        return result
    if op == "$type": return bson_type(ev(raw))
    if op == "$isArray": return isinstance(ev(raw), (list, tuple))
    if op in ("$eq", "$ne", "$gt", "$gte", "$lt", "$lte"):
        left, right = args(raw, 2)
        if op == "$eq": return bson_equal(left, right)
        if op == "$ne": return not bson_equal(left, right)
        c = compare_values(left, right)
        return {"$gt": c > 0, "$gte": c >= 0, "$lt": c < 0, "$lte": c <= 0}[op]
    if op in ("$and", "$or", "$not"):
        values = args(raw, 1 if op == "$not" else None)
        return not _truth(values[0]) if op == "$not" else all(map(_truth, values)) if op == "$and" else any(map(_truth, values))
    if op == "$in":
        item = ev(raw[0])
        array_expr = raw[1]
        marker = ("membership", id(array_expr))
        cached = context.get(marker)
        if cached is not None and cached[0] is array_expr:
            return _membership_contains(item, cached[1])
        constant = _constant_expression(array_expr)
        array = ev(array_expr)
        if not isinstance(array, (list, tuple)): _error("$in expression requires array")
        if constant: index = _expression_membership(array_expr, array, context)
        elif isinstance(array_expr, str) and array_expr.startswith("$$"): index = _expression_membership(array, array, context)
        else: index = _membership_index(array, query=False)
        return _membership_contains(item, index)
    if op in ("$setEquals", "$setIsSubset", "$setUnion"):
        values = args(raw)
        if any(not isinstance(v, (list, tuple)) for v in values): _error("Set expression requires arrays")
        if op == "$setUnion": return deepcopy(_unique([item for value in values for item in value]))
        if op == "$setIsSubset" and len(values) != 2: _error("Set subset arity")
        first = _unique(values[0]) if values else []
        if op == "$setIsSubset": return all(any(bson_equal(item, v) for v in values[1]) for item in first)
        return all(len(first) == len(_unique(value)) and all(any(bson_equal(item, v) for v in value) for item in first) for value in values[1:])
    if op in ("$anyElementTrue", "$allElementsTrue"):
        (values,) = args(raw, 1)
        if not isinstance(values, (list, tuple)): _error("Boolean array expression requires array")
        return any(map(_truth, values)) if op == "$anyElementTrue" else all(map(_truth, values))
    if op == "$arrayElemAt":
        array, index = args(raw, 2)
        if _null(array): return None
        if not isinstance(array, (list, tuple)) or not isinstance(index, int) or isinstance(index, bool): _error("Invalid array index")
        return array[index] if -len(array) <= index < len(array) else MISSING
    if op == "$indexOfArray":
        values = args(raw)
        if not 2 <= len(values) <= 4: _error("Invalid indexOfArray arity")
        array, item = values[:2]
        if _null(array): return None
        if not isinstance(array, (list, tuple)): _error("indexOfArray requires array")
        start = values[2] if len(values) > 2 else 0; end = values[3] if len(values) > 3 else len(array)
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (start, end)): _error("Invalid indexOfArray bounds")
        return next((i for i in range(start, min(end, len(array))) if bson_equal(array[i], item)), -1)
    if op == "$size":
        value = ev(raw)
        if not isinstance(value, (list, tuple)): _error("$size expression requires array")
        return len(value)
    if op == "$slice":
        values = args(raw)
        if len(values) not in (2, 3) or not isinstance(values[0], (list, tuple)): _error("Invalid slice")
        array = values[0]
        if len(values) == 2: return array[:values[1]] if values[1] >= 0 else array[values[1]:]
        start, count = values[1:]; start = max(0, len(array)+start) if start < 0 else start
        if count <= 0: _error("Invalid slice count")
        return array[start:start+count]
    if op == "$concatArrays":
        values = args(raw)
        if any(_null(v) for v in values): return None
        if any(not isinstance(v, (list, tuple)) for v in values): _error("concatArrays requires arrays")
        return [deepcopy(item) for value in values for item in value]
    if op == "$objectToArray":
        value = ev(raw)
        if _null(value): return None
        if not isinstance(value, Mapping): _error("objectToArray requires object")
        return [{"k": k, "v": deepcopy(v)} for k, v in value.items()]
    if op == "$arrayToObject":
        value = ev(raw)
        if _null(value): return None
        if not isinstance(value, (list, tuple)): _error("arrayToObject requires array")
        result = {}
        for item in value:
            if isinstance(item, Mapping) and set(item) == {"k", "v"}: key, val = item["k"], item["v"]
            elif isinstance(item, (list, tuple)) and len(item) == 2: key, val = item
            else: _error("Invalid arrayToObject entry")
            if not isinstance(key, str) or "\x00" in key: _error("Invalid arrayToObject key")
            result[key] = deepcopy(val)
        return result
    if op == "$getField":
        value = ev(raw.get("input", "$$CURRENT")) if isinstance(raw, Mapping) else doc
        field = ev(raw["field"]) if isinstance(raw, Mapping) else ev(raw)
        if _null(value): return None
        if not isinstance(value, Mapping) or not isinstance(field, str): _error("Invalid getField")
        return value.get(field, MISSING)
    if op == "$mergeObjects":
        values = args(raw) if isinstance(raw, (list, tuple)) else [ev(raw)]
        result = {}
        for value in values:
            if _null(value): continue
            if not isinstance(value, Mapping): _error("mergeObjects requires objects")
            result.update(deepcopy(value))
        return result
    return _evaluate_scalar(op, raw, ev, args)


def _evaluate_scalar(op, raw, ev, args):
    if op == "$concat":
        values = args(raw)
        if any(_null(v) for v in values): return None
        if any(not isinstance(v, str) for v in values): _error("concat requires strings")
        return "".join(values)
    if op == "$trim":
        value = ev(raw["input"])
        if _null(value): return None
        if not isinstance(value, str): _error("trim requires string")
        return value.strip(ev(raw["chars"])) if "chars" in raw else value.strip()
    if op == "$strLenCP":
        value = ev(raw)
        if not isinstance(value, str): _error("strLenCP requires string")
        return len(value)
    if op == "$substrBytes":
        value, start, count = args(raw, 3)
        if _null(value): value = ""
        if not isinstance(value, str): value = _convert(value, "string")
        if start < 0 or count < 0: _error("Invalid substring bounds")
        try: return value.encode("utf8")[start:start+count].decode("utf8")
        except UnicodeDecodeError: _error("Substring splits a UTF-8 sequence")
    if op in ("$regexMatch", "$regexFind"):
        value = ev(raw["input"])
        if not isinstance(value, str): _error("Regex expression requires string")
        found = _regex(ev(raw["regex"]), ev(raw.get("options", ""))).search(value)
        if op == "$regexMatch": return found is not None
        return {"match": found.group(0), "idx": found.start(), "captures": list(found.groups())} if found else None
    if op == "$convert":
        value = ev(raw["input"])
        if _null(value): return ev(raw["onNull"]) if "onNull" in raw else None
        try: return _convert(value, ev(raw["to"]))
        except (ValueError, TypeError, OverflowError):
            if "onError" in raw: return ev(raw["onError"])
            _error("Conversion failed")
    if op in ("$toInt", "$toLong", "$toString", "$toDouble", "$toDate", "$toBool"):
        return _convert(ev(raw), {"$toInt": "int", "$toLong": "long", "$toString": "string", "$toDouble": "double", "$toDate": "date", "$toBool": "bool"}[op])
    if op == "$pow":
        values = args(raw, 2)
        if any(_null(value) for value in values): return None
        if any(not _number(value) for value in values): _error("Power requires numeric arguments")
        base, power = values
        if any(isinstance(value, (Decimal, Decimal128)) for value in values):
            from decimal import localcontext
            from bson.decimal128 import create_decimal128_context
            base, power = _decimal(base), _decimal(power)
            if base.is_zero() and power < 0: _error("Zero cannot have a negative exponent")
            with localcontext(create_decimal128_context()) as context:
                result = context.power(base, power)
            return Decimal128(result)
        if base == 0 and power < 0: _error("Zero cannot have a negative exponent")
        try:
            if isinstance(base, float) or isinstance(power, float) or power < 0:
                return math.pow(base, power)
            # Avoid constructing an unbounded Python integer on corrupt data.
            if power > 4096 and abs(base) > 1:
                return math.copysign(float("inf"), -1 if base < 0 and power % 2 else 1)
            result = base ** power
            if -(2**31) <= result < 2**31 and not any(isinstance(v, Int64) for v in values): return result
            if -(2**63) <= result < 2**63: return Int64(result)
            return float(result)
        except ValueError: return float("nan")
        except OverflowError: return math.copysign(float("inf"), -1 if base < 0 and power % 2 else 1)
    if op in ("$sin", "$cos", "$asin", "$degreesToRadians"):
        value = ev(raw)
        if _null(value): return None
        if not _number(value): _error("Trigonometry requires a numeric argument")
        if isinstance(value, (Decimal, Decimal128)):
            # Marketplace coordinates are validated BSON doubles. Do not
            # silently round an unreviewed decimal128 trigonometric input.
            _error("Decimal trigonometry has no reviewed exact implementation")
        try:
            return {"$sin": math.sin, "$cos": math.cos, "$asin": math.asin,
                    "$degreesToRadians": math.radians}[op](value)
        except (ValueError, OverflowError): _error("Trigonometric argument is outside its domain")
    if op in ("$add", "$subtract", "$multiply", "$divide", "$mod"):
        values = args(raw, 2 if op in ("$subtract", "$divide", "$mod") else None)
        if any(_null(v) for v in values): return None
        if op == "$add":
            dates = [v for v in values if isinstance(v, datetime)]
            if len(dates) > 1 or any(not _number(v) and not isinstance(v, datetime) for v in values): _error("Invalid addition")
            total = sum(v for v in values if not isinstance(v, datetime))
            return dates[0]+timedelta(milliseconds=round(total)) if dates else total
        if op == "$subtract" and isinstance(values[0], datetime):
            return Int64(_millis(values[0])-_millis(values[1])) if isinstance(values[1], datetime) else values[0]-timedelta(milliseconds=values[1])
        if any(not _number(v) for v in values): _error("Arithmetic requires numbers")
        if op == "$subtract": return values[0]-values[1]
        if op == "$multiply": return math.prod(values)
        if values[1] == 0: _error("Division by zero")
        if op == "$divide": return values[0]/values[1]
        return math.fmod(values[0], values[1])
    if op in ("$floor", "$ceil", "$sqrt", "$abs"):
        value = ev(raw)
        if _null(value): return None
        if not _number(value): _error("Numeric expression requires number")
        if op == "$sqrt": return math.sqrt(value)
        if op == "$abs": return abs(value)
        result = math.floor(value) if op == "$floor" else math.ceil(value)
        return float(result) if isinstance(value, float) else result
    if op == "$round":
        values = args(raw); value = values[0]; places = values[1] if len(values) > 1 else 0
        if _null(value): return None
        if not _number(value) or not isinstance(places, int) or isinstance(places, bool) or not -20 < places < 100: _error("Invalid round")
        return round(value, places)
    if op in ("$sum", "$avg", "$min", "$max"):
        is_list = isinstance(raw, (list, tuple)); values = args(raw) if is_list else [ev(raw)]
        if not is_list and isinstance(values[0], (list, tuple)): values = [v for v in values[0] if _number(v)]
        if op in ("$sum", "$avg"):
            values = [v for v in values if _number(v)]
            return sum(values) if op == "$sum" else sum(values)/len(values) if values else None
        values = [v for v in values if not _null(v)]
        return sorted(values, key=cmp_to_key(compare_values))[0 if op == "$min" else -1] if values else None
    if op in ("$first", "$last"):
        value = ev(raw)
        if _null(value): return None
        if not isinstance(value, (list, tuple)): _error("Array first/last requires array")
        return value[0 if op == "$first" else -1] if value else MISSING
    if op == "$dateToString":
        value = ev(raw["date"])
        if _null(value): return ev(raw["onNull"]) if "onNull" in raw else None
        if not isinstance(value, datetime): _error("dateToString requires date")
        if ev(raw.get("timezone", "UTC")) not in ("UTC", "GMT", "Etc/UTC", "Z"): _error("Unsupported date timezone")
        return _utc(value).strftime(ev(raw.get("format", "%Y-%m-%dT%H:%M:%S.%LZ")).replace("%L", f"{value.microsecond//1000:03d}"))
    _error("Unsupported expression operator")


def _include_path(value, parts):
    if not parts: return deepcopy(value)
    if isinstance(value, Mapping):
        key = parts[0]
        if key not in value: return MISSING
        found = _include_path(value[key], parts[1:])
        return {key: found} if found is not MISSING else {}
    if isinstance(value, list): return [part for item in value if (part := _include_path(item, parts)) is not MISSING]
    return MISSING


def _merge_projection(target, source):
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict): _merge_projection(target[key], value)
        elif key in target and isinstance(target[key], list) and isinstance(value, list) and len(target[key]) == len(value):
            for a, b in zip(target[key], value):
                if isinstance(a, dict) and isinstance(b, dict): _merge_projection(a, b)
        else: target[key] = deepcopy(value)


def _flag(value, flag):
    return isinstance(value, (bool, int)) and value == flag


def validate_projection(projection, *, find=True):
    if projection is None: return
    if isinstance(projection, (list, tuple)):
        if any(not isinstance(value, str) for value in projection): _error("Invalid field projection")
        return
    if not isinstance(projection, Mapping): _error("Projection must be object")
    include = any(not _flag(value, 0) for key, value in projection.items() if key != "_id")
    if include and any(_flag(value, 0) for key, value in projection.items() if key != "_id"): _error("Mixed projection modes")
    for path, value in projection.items():
        if not isinstance(path, str) or not path or path.startswith("$"): _error("Invalid projection path")
        if find and isinstance(value, Mapping) and set(value) == {"$elemMatch"}:
            condition = value["$elemMatch"]
            if not isinstance(condition, Mapping): _error("Projection elemMatch requires object")
            if all(key in _QUERY_OPS for key in condition): _validate_condition(condition)
            else: validate_query(condition)
        elif not (_flag(value, 0) or _flag(value, 1)): validate_expression(value)


def project(doc, projection, *, now=None):
    return compile_projection(projection, now=now)(doc)


def compile_projection(projection, *, now=None, variables=None):
    """Validate an owned plan once; retain only its immutable constant indexes.

    The callable belongs to one repository operation. Dynamic document/let/map
    arrays get a fresh evaluation context for every row, never a shared cache.
    Constant indexes are populated lazily so an unused expression branch keeps
    its normal evaluation semantics (including errors only when evaluated).
    """
    validate_projection(projection)
    projection, variables, now = deepcopy(projection), deepcopy(variables or {}), _clock(now)
    constants, indexes = {}, {}

    def inspect(value):
        if isinstance(value, Mapping):
            if set(value) in ({"$literal"}, {"$elemMatch"}): return
            if set(value) == {"$in"}:
                expression = value["$in"][1]
                if len(constants) < 128 and _constant_expression(expression): constants[id(expression)] = expression
            for item in value.values(): inspect(item)
        elif isinstance(value, (list, tuple)):
            for item in value: inspect(item)

    inspect(projection)

    def compiled(doc):
        context = dict(indexes)
        result = _project(doc, projection, now=now, variables=variables, context=context)
        for marker, entry in context.items():
            if (len(indexes) < 128 and marker[0] == "membership"
                    and constants.get(marker[1], MISSING) is entry[0]):
                indexes.setdefault(marker, entry)
        return result

    return compiled


def _project(doc, projection, *, now, variables, context=None):
    context = {} if context is None else context
    if projection is None: return deepcopy(doc)
    if isinstance(projection, (list, tuple)): projection = {name: 1 for name in projection}
    if not projection: return deepcopy(doc)
    include = any(not _flag(value, 0) for key, value in projection.items() if key != "_id") or (set(projection) == {"_id"} and _flag(projection["_id"], 1))
    result = {} if include else deepcopy(doc)
    if include and not _flag(projection.get("_id", 1), 0) and "_id" in doc: result["_id"] = deepcopy(doc["_id"])
    for path, value in projection.items():
        if _flag(value, 0): _unset_path(result, path)
        elif _flag(value, 1):
            if include:
                part = _include_path(doc, path.split("."))
                if part is not MISSING: _merge_projection(result, part)
        elif isinstance(value, Mapping) and set(value) == {"$elemMatch"}:
            array, condition = get_path(doc, path), value["$elemMatch"]
            matched = MISSING
            if isinstance(array, (list, tuple)):
                mode = all(key in _QUERY_OPS for key in condition)
                matched = next((item for item in array if _condition([item], condition, now, expand_arrays=False)), MISSING) if mode else next(
                    (item for item in array if isinstance(item, Mapping) and matches(item, condition, now=now)), MISSING)
            _set_path(result, path, [matched] if matched is not MISSING else MISSING)
        else: _set_path(result, path, _evaluate(value, doc, variables, now, context))
    return result


def _sort_pairs(sort):
    pairs = list(sort.items()) if isinstance(sort, Mapping) else [(sort, 1)] if isinstance(sort, str) else list(sort)
    if any(not isinstance(field, str) or direction not in (1, -1) for field, direction in pairs): _error("Invalid document sort")
    return pairs


def sort_documents(rows, sort):
    pairs = _sort_pairs(sort)
    def key(doc, field, direction):
        values = path_values(doc, field)
        flat = [item for value in values for item in (list(value) if isinstance(value, (list, tuple)) and value else [MinKey()] if isinstance(value, (list, tuple)) else [value])]
        flat = [None if value is MISSING else value for value in flat] or [None]
        return sorted(flat, key=cmp_to_key(compare_values))[0 if direction == 1 else -1]
    def compare(a, b):
        for field, direction in pairs:
            result = compare_values(key(a, field, direction), key(b, field, direction))
            if result: return result*direction
        return 0
    return sorted(rows, key=cmp_to_key(compare))


_ACCUMULATORS = {"$sum", "$avg", "$first", "$last", "$push", "$addToSet", "$min", "$max"}


def validate_pipeline(pipeline):
    if not isinstance(pipeline, (list, tuple)): _error("Pipeline must be array")
    for stage in pipeline:
        if not isinstance(stage, Mapping) or len(stage) != 1: _error("Invalid pipeline stage")
        op, spec = next(iter(stage.items()))
        if op == "$match": validate_query(spec)
        elif op in ("$set", "$addFields"):
            if not isinstance(spec, Mapping): _error("Set stage requires object")
            for path, expr in spec.items():
                if not isinstance(path, str) or not path or path.startswith("$"): _error("Invalid set path")
                validate_expression(expr)
        elif op == "$project":
            if not spec: _error("Empty aggregation projection")
            validate_projection(spec, find=False)
        elif op == "$unset":
            paths = [spec] if isinstance(spec, str) else spec
            if not isinstance(paths, (list, tuple)) or not paths or any(not isinstance(p, str) for p in paths): _error("Invalid unset stage")
        elif op == "$sort": _sort_pairs(spec)
        elif op in ("$skip", "$limit"):
            if not isinstance(spec, int) or isinstance(spec, bool) or spec < 0 or (op == "$limit" and spec == 0): _error("Invalid row bound")
        elif op == "$count":
            if not isinstance(spec, str) or not spec or "." in spec or spec.startswith("$"): _error("Invalid count field")
        elif op == "$group":
            if not isinstance(spec, Mapping) or "_id" not in spec: _error("Group requires _id")
            validate_expression(spec["_id"])
            for name, acc in spec.items():
                if name == "_id": continue
                if not isinstance(acc, Mapping) or len(acc) != 1: _error("Invalid group accumulator")
                accumulator, expr = next(iter(acc.items()))
                if accumulator not in _ACCUMULATORS: _error("Unsupported group accumulator")
                validate_expression(expr)
        elif op == "$unwind":
            config = {"path": spec} if isinstance(spec, str) else spec
            if not isinstance(config, Mapping) or set(config)-{"path", "preserveNullAndEmptyArrays", "includeArrayIndex"}: _error("Invalid unwind configuration")
            if not isinstance(config.get("path"), str) or not config["path"].startswith("$"): _error("Invalid unwind path")
        elif op == "$lookup":
            if not isinstance(spec, Mapping) or set(spec)-{"from", "as", "localField", "foreignField", "let", "pipeline"}: _error("Invalid lookup")
            if not all(isinstance(spec.get(key), str) for key in ("from", "as")): _error("Lookup requires from/as")
            if ("localField" in spec) != ("foreignField" in spec): _error("Incomplete lookup equality")
            for expr in spec.get("let", {}).values(): validate_expression(expr)
            validate_pipeline(spec.get("pipeline", []))
        elif op == "$unionWith":
            config = {"coll": spec} if isinstance(spec, str) else spec
            if not isinstance(config, Mapping) or set(config)-{"coll", "pipeline"} or not isinstance(config.get("coll"), str): _error("Invalid union")
            validate_pipeline(config.get("pipeline", []))
        elif op == "$facet":
            if not isinstance(spec, Mapping): _error("Invalid facet")
            for stages in spec.values(): validate_pipeline(stages)
        else: _error("Unsupported aggregation stage")


def _group(rows, spec, now, variables):
    groups, buckets = [], {}
    for doc in rows:
        key = evaluate(spec["_id"], doc, variables=variables, now=now)
        key = None if key is MISSING else key
        bucket = buckets.setdefault(bson_hash_key(key), [])
        group = next((entry for entry in bucket if bson_equal(entry[0], key)), None)
        if group is None:
            group = [deepcopy(key), []]; groups.append(group); bucket.append(group)
        group[1].append(doc)
    output = []
    for key, documents in groups:
        row = {"_id": key}
        for name, acc in spec.items():
            if name == "_id": continue
            op, expr = next(iter(acc.items()))
            values = [evaluate(expr, doc, variables=variables, now=now) for doc in documents]
            present = [v for v in values if v is not MISSING]
            numeric = [v for v in present if _number(v)]
            if op == "$sum": value = sum(numeric)
            elif op == "$avg": value = sum(numeric)/len(numeric) if numeric else None
            elif op == "$first": value = None if values[0] is MISSING else values[0]
            elif op == "$last": value = None if values[-1] is MISSING else values[-1]
            elif op == "$push": value = present
            elif op == "$addToSet": value = _unique(present)
            elif op in ("$min", "$max"):
                nonnull = [v for v in present if v is not None]
                value = sorted(nonnull, key=cmp_to_key(compare_values))[0 if op == "$min" else -1] if nonnull else None
            else: _error("Unsupported group accumulator")
            row[name] = deepcopy(value)
        output.append(row)
    return output


def _lookup_index(foreign, field):
    index = {}
    for position, doc in enumerate(foreign):
        for value in path_values(doc, field) or [None]:
            for token in ([value, *value] if isinstance(value, (list, tuple)) else [value]):
                index.setdefault(bson_hash_key(token), set()).add(position)
    return index


def _lookup_candidates(foreign, index, local):
    positions = set()
    for value in local:
        for token in ([value, *value] if isinstance(value, (list, tuple)) else [value]):
            positions.update(index.get(bson_hash_key(token), ()))
    return [foreign[position] for position in sorted(positions)]


def aggregate_documents(rows, pipeline, *, now=None, lookup=None, variables=None):
    validate_pipeline(pipeline)
    now, variables = _clock(now), variables or {}
    result = [deepcopy(doc) for doc in rows]
    for stage in pipeline:
        op, spec = next(iter(stage.items()))
        if op == "$match": result = match_documents(result, spec, variables=variables, now=now)
        elif op in ("$set", "$addFields"):
            changed = []
            for doc in result:
                new = deepcopy(doc)
                for key, expr in spec.items(): _set_path(new, key, evaluate(expr, doc, variables=variables, now=now))
                changed.append(new)
            result = changed
        elif op == "$unset":
            for doc in result:
                for path in ([spec] if isinstance(spec, str) else spec): _unset_path(doc, path)
        elif op == "$project": result = [_project(doc, spec, now=now, variables=variables) for doc in result]
        elif op == "$sort": result = sort_documents(result, spec)
        elif op == "$skip": result = result[spec:]
        elif op == "$limit": result = result[:spec]
        elif op == "$count": result = [{spec: len(result)}] if result else []
        elif op == "$group": result = _group(result, spec, now, variables)
        elif op == "$unwind":
            config = {"path": spec} if isinstance(spec, str) else spec
            path, unwound = config["path"][1:], []
            for doc in result:
                value = get_path(doc, path)
                if isinstance(value, list) and value:
                    for index, item in enumerate(value):
                        new = deepcopy(doc); _set_path(new, path, item)
                        if "includeArrayIndex" in config: _set_path(new, config["includeArrayIndex"], Int64(index))
                        unwound.append(new)
                elif not _null(value) and not isinstance(value, list):
                    new = deepcopy(doc)
                    if "includeArrayIndex" in config: _set_path(new, config["includeArrayIndex"], None)
                    unwound.append(new)
                elif config.get("preserveNullAndEmptyArrays"):
                    new = deepcopy(doc)
                    if isinstance(value, list): _unset_path(new, path)
                    if "includeArrayIndex" in config: _set_path(new, config["includeArrayIndex"], None)
                    unwound.append(new)
            result = unwound
        elif op == "$lookup":
            if lookup is None: _error("Lookup resolver is required")
            foreign, joined = list(lookup(spec["from"])), []
            equality_field, variable_expr = spec.get("foreignField"), None
            if equality_field is None and spec.get("pipeline"):
                first = spec["pipeline"][0].get("$match", {}).get("$expr", {})
                eq = first.get("$eq") if isinstance(first, Mapping) else None
                if isinstance(eq, (list, tuple)) and len(eq) == 2:
                    for field, variable in (eq, list(reversed(eq))):
                        if isinstance(field, str) and field.startswith("$") and not field.startswith("$$") and isinstance(variable, str) and variable.startswith("$$"):
                            equality_field, variable_expr = field[1:], variable
                            break
            equality_index = _lookup_index(foreign, equality_field) if equality_field is not None else None
            for doc in result:
                candidates = foreign
                scope = {**variables, **{name: evaluate(expr, doc, variables=variables, now=now) for name, expr in spec.get("let", {}).items()}}
                if "localField" in spec:
                    local = path_values(doc, spec["localField"]) or [None]
                    candidates = [other for other in _lookup_candidates(foreign, equality_index, local) if any(
                        _equal_query(a, b) or _equal_query(b, a) for a in local for b in (path_values(other, equality_field) or [None]))]
                elif variable_expr is not None:
                    local_value = evaluate(variable_expr, doc, variables=scope, now=now)
                    # Candidate selection may overselect null/missing; the
                    # unchanged $expr pipeline distinguishes them exactly.
                    candidates = _lookup_candidates(foreign, equality_index, [None if local_value is MISSING else local_value])
                if "pipeline" in spec: candidates = aggregate_documents(candidates, spec["pipeline"], now=now, lookup=lookup, variables=scope)
                new = deepcopy(doc); _set_path(new, spec["as"], candidates); joined.append(new)
            result = joined
        elif op == "$unionWith":
            if lookup is None: _error("Union resolver is required")
            config = {"coll": spec} if isinstance(spec, str) else spec
            result += aggregate_documents(lookup(config["coll"]), config.get("pipeline", []), now=now, lookup=lookup, variables=variables)
        elif op == "$facet": result = [{name: aggregate_documents(result, stages, now=now, lookup=lookup, variables=variables) for name, stages in spec.items()}]
        else: _error("Unsupported aggregation stage")
    return result
