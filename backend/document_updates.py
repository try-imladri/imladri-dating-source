"""Pure, strict document updates for the PostgreSQL application store.

The store owns transaction locks, predicate rechecks, uniqueness and commit.
This module never mutates its input and never performs an external side effect.
"""
from copy import deepcopy
from datetime import datetime
from functools import cmp_to_key
from numbers import Number

from .document_queries import (
    MISSING, UnsupportedDocumentOperation, bson_equal, compare_values, evaluate,
    get_path, matches, sort_documents, validate_expression, validate_query,
)


class DocumentUpdateError(ValueError):
    """A supported update has an invalid operand, path or immutable ID change."""


def _path(path):
    if not isinstance(path, str) or not path or "\x00" in path:
        raise DocumentUpdateError("Expected a nonempty document update path")
    parts = path.split(".")
    if any(not part or part.startswith("$") for part in parts):
        raise UnsupportedDocumentOperation("Positional or empty update paths are unsupported")
    return parts


def _paths_unique(paths):
    seen = []
    for path in paths:
        parts = _path(path)
        if any(parts[:len(other)] == other or other[:len(parts)] == parts for other in seen):
            raise DocumentUpdateError("Conflicting document update paths")
        seen.append(parts)


def _parent(document, path, *, create):
    parts = _path(path)
    parent = document
    for part in parts[:-1]:
        if isinstance(parent, dict):
            if part not in parent:
                if not create:
                    return None, None
                parent[part] = {}
            parent = parent[part]
        elif isinstance(parent, list):
            if not part.isdigit():
                raise DocumentUpdateError("An array update path must contain an index")
            index = int(part)
            if index >= len(parent):
                if not create:
                    return None, None
                parent.extend([None] * (index + 1 - len(parent)))
                parent[index] = {}
            parent = parent[index]
        else:
            if not create:
                return None, None
            raise DocumentUpdateError("Cannot traverse a scalar update path")
    return parent, parts[-1]


def _set(document, path, value):
    parent, leaf = _parent(document, path, create=True)
    if isinstance(parent, dict):
        parent[leaf] = deepcopy(value)
    elif isinstance(parent, list) and leaf.isdigit():
        index = int(leaf)
        if index >= len(parent):
            parent.extend([None] * (index + 1 - len(parent)))
        parent[index] = deepcopy(value)
    else:
        raise DocumentUpdateError("Cannot assign through a scalar or nonnumeric array path")


def _unset(document, path):
    parent, leaf = _parent(document, path, create=False)
    if isinstance(parent, dict):
        parent.pop(leaf, None)
    elif isinstance(parent, list) and leaf.isdigit():
        index = int(leaf)
        if index < len(parent):
            parent[index] = None
    elif parent is not None and not isinstance(parent, list):
        return


def _numeric(value):
    return isinstance(value, Number) and not isinstance(value, bool)


def _array(document, path):
    value = get_path(document, path)
    if value is MISSING:
        return []
    if not isinstance(value, list):
        raise DocumentUpdateError("An array modifier requires an existing array or missing field")
    return deepcopy(value)


def _push(array, operand):
    if not isinstance(operand, dict) or "$each" not in operand:
        return [*array, deepcopy(operand)]
    if set(operand) - {"$each", "$slice", "$position", "$sort"}:
        raise UnsupportedDocumentOperation("Unknown $push modifier")
    values = operand["$each"]
    if not isinstance(values, list):
        raise DocumentUpdateError("$push $each must be an array")
    position = operand.get("$position", len(array))
    if type(position) is not int:
        raise DocumentUpdateError("$push $position must be an integer")
    position = min(position, len(array)) if position >= 0 else max(0, len(array) + position)
    result = [*array[:position], *deepcopy(values), *array[position:]]
    if "$sort" in operand:
        order = operand["$sort"]
        if type(order) is int and order in (-1, 1):
            result.sort(key=cmp_to_key(lambda a, b: order * compare_values(a, b)))
        elif isinstance(order, dict) and order and all(type(v) is int and v in (-1, 1) for v in order.values()):
            if not all(isinstance(value, dict) for value in result):
                raise DocumentUpdateError("Field-sorted $push requires documents")
            result = sort_documents(result, list(order.items()))
        else:
            raise DocumentUpdateError("Invalid $push $sort order")
    if "$slice" in operand:
        limit = operand["$slice"]
        if type(limit) is not int:
            raise DocumentUpdateError("$push $slice must be an integer")
        result = result[:limit] if limit >= 0 else result[limit:]
    return result


def _document_pull_condition(condition):
    return isinstance(condition, dict) and (not any(key.startswith("$") for key in condition)
                                           or bool(set(condition) & {"$and", "$or", "$nor"}))


def _pull_matches(value, condition, now):
    if _document_pull_condition(condition):
        return isinstance(value, dict) and matches(value, condition, now=now)
    return matches({"value": value}, {"value": condition}, now=now)


def _clock(now):
    if not isinstance(now, datetime):
        raise DocumentUpdateError("A database clock datetime is required for time-based updates")
    return now


def _requires_clock(value):
    if isinstance(value, str):
        return value == "$$NOW" or value.startswith("$$NOW.")
    if isinstance(value, dict):
        if set(value) == {"$literal"}:
            return False
        return any(_requires_clock(item) for item in value.values())
    return isinstance(value, list) and any(_requires_clock(item) for item in value)


def validate_update(update):
    """Validate syntax without a matching document or evaluating a chosen branch."""
    if isinstance(update, list):
        for stage in update:
            if not isinstance(stage, dict) or len(stage) != 1:
                raise DocumentUpdateError("An update pipeline stage must have exactly one operator")
            operator, fields = next(iter(stage.items()))
            if operator in ("$set", "$addFields"):
                if not isinstance(fields, dict):
                    raise DocumentUpdateError("An update pipeline $set requires a document")
                _paths_unique(fields)
                for expression in fields.values():
                    validate_expression(expression)
            elif operator == "$unset":
                paths = [fields] if isinstance(fields, str) else fields
                if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                    raise DocumentUpdateError("An update pipeline $unset requires a path or array of paths")
                _paths_unique(paths)
            else:
                raise UnsupportedDocumentOperation("Unsupported update pipeline stage: " + str(operator))
        return
    if not isinstance(update, dict) or not update or any(not isinstance(key, str) or not key.startswith("$") for key in update):
        raise DocumentUpdateError("Collection update requires an operator document or pipeline")
    supported = {"$set", "$unset", "$inc", "$min", "$max", "$setOnInsert", "$addToSet", "$pull", "$push", "$currentDate"}
    if set(update) - supported:
        raise UnsupportedDocumentOperation("Unsupported document update operator: " + str(sorted(set(update) - supported)))
    if any(not isinstance(fields, dict) for fields in update.values()):
        raise DocumentUpdateError("Update operator operands must be documents")
    _paths_unique([path for fields in update.values() for path in fields])
    for operator, fields in update.items():
        for operand in fields.values():
            if operator == "$inc" and not _numeric(operand):
                raise DocumentUpdateError("$inc requires numeric operands")
            if operator == "$currentDate" and operand is not True and operand != {"$type": "date"}:
                raise UnsupportedDocumentOperation("Only date-valued $currentDate is supported")
            if operator in ("$push", "$addToSet") and isinstance(operand, dict) and "$each" in operand:
                allowed = {"$each", "$slice", "$position", "$sort"} if operator == "$push" else {"$each"}
                if set(operand) - allowed:
                    raise UnsupportedDocumentOperation("Unknown array update modifier")
                if not isinstance(operand["$each"], list):
                    raise DocumentUpdateError("An array $each modifier requires an array")
                if any(type(operand[key]) is not int for key in ("$slice", "$position") if key in operand):
                    raise DocumentUpdateError("Array slice and position modifiers require integers")
                if "$sort" in operand:
                    sort = operand["$sort"]
                    if not (type(sort) is int and sort in (-1, 1)):
                        if not isinstance(sort, dict) or not sort or any(type(value) is not int or value not in (-1, 1) for value in sort.values()):
                            raise DocumentUpdateError("Invalid $push $sort order")
                        for path in sort:
                            _path(path)
            if operator == "$pull":
                if _document_pull_condition(operand):
                    validate_query(operand)
                else:
                    validate_query({"value": operand})


def _pipeline(document, pipeline, now):
    result = deepcopy(document)
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise DocumentUpdateError("An update pipeline stage must have exactly one operator")
        operator, fields = next(iter(stage.items()))
        if operator in ("$set", "$addFields"):
            if not isinstance(fields, dict):
                raise DocumentUpdateError("An update pipeline $set requires a document")
            _paths_unique(fields)
            if _requires_clock(fields):
                _clock(now)
            before = deepcopy(result)
            # Every expression sees the same stage snapshot, including sibling fields.
            values = {path: evaluate(expr, before, now=now) for path, expr in fields.items()}
            for path, value in values.items():
                if value is MISSING:
                    _unset(result, path)
                else:
                    _set(result, path, value)
        elif operator == "$unset":
            paths = [fields] if isinstance(fields, str) else fields
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise DocumentUpdateError("An update pipeline $unset requires a path or array of paths")
            _paths_unique(paths)
            for path in paths:
                _unset(result, path)
        else:
            raise UnsupportedDocumentOperation("Unsupported update pipeline stage: " + str(operator))
    return result


def apply_update(document, update, *, inserting=False, now=None):
    """Return the updated copy; caller commits it atomically under a database lock."""
    validate_update(update)
    if not isinstance(document, dict):
        raise DocumentUpdateError("Expected a document")
    if isinstance(update, list):
        result = _pipeline(document, update, now)
    else:
        if not isinstance(update, dict) or not update or any(not isinstance(key, str) or not key.startswith("$") for key in update):
            raise DocumentUpdateError("Collection update requires an operator document or pipeline")
        supported = {"$set", "$unset", "$inc", "$min", "$max", "$setOnInsert", "$addToSet", "$pull", "$push", "$currentDate"}
        if set(update) - supported:
            raise UnsupportedDocumentOperation("Unsupported document update operator: " + str(sorted(set(update) - supported)))
        if any(not isinstance(fields, dict) for fields in update.values()):
            raise DocumentUpdateError("Update operator operands must be documents")
        _paths_unique([path for fields in update.values() for path in fields])
        result = deepcopy(document)
        # Mongo applies disjoint modifier paths in path order across operators.
        actions = [(path, operator, operand) for operator, fields in update.items() for path, operand in fields.items()]
        path_order = lambda action: tuple((0, int(p)) if p.isdigit() else (1, p) for p in action[0].split("."))
        for path, operator, operand in sorted(actions, key=path_order):
            if operator == "$setOnInsert" and not inserting:
                continue
            current = get_path(result, path)
            if operator in ("$set", "$setOnInsert"):
                _set(result, path, operand)
            elif operator == "$unset":
                _unset(result, path)
            elif operator == "$inc":
                if not _numeric(operand) or (current is not MISSING and not _numeric(current)):
                    raise DocumentUpdateError("$inc requires numeric operands and fields")
                _set(result, path, operand if current is MISSING else current + operand)
            elif operator in ("$min", "$max"):
                compare = compare_values(current, operand) if current is not MISSING else None
                if current is MISSING or (operator == "$min" and compare > 0) or (operator == "$max" and compare < 0):
                    _set(result, path, operand)
            elif operator == "$addToSet":
                array = _array(result, path)
                if isinstance(operand, dict) and "$each" in operand:
                    if set(operand) != {"$each"} or not isinstance(operand["$each"], list):
                        raise DocumentUpdateError("$addToSet only supports an array-valued $each modifier")
                    values = operand["$each"]
                else:
                    values = [operand]
                for value in values:
                    if not any(bson_equal(value, existing) for existing in array):
                        array.append(deepcopy(value))
                _set(result, path, array)
            elif operator == "$push":
                _set(result, path, _push(_array(result, path), operand))
            elif operator == "$pull":
                if current is not MISSING:
                    _set(result, path, [item for item in _array(result, path) if not _pull_matches(item, operand, now)])
            elif operator == "$currentDate":
                if operand is not True and operand != {"$type": "date"}:
                    raise UnsupportedDocumentOperation("Only date-valued $currentDate is supported")
                _set(result, path, _clock(now))
    if "_id" in document and ("_id" not in result or not bson_equal(document["_id"], result["_id"])):
        raise DocumentUpdateError("The document _id is immutable")
    return result


def upsert_document(query):
    """Seed equality predicates only; range/alternative clauses never invent data."""
    if not isinstance(query, dict):
        raise DocumentUpdateError("Expected an upsert query document")
    validate_query(query)
    assignments = {}
    def gather(clause):
        if not isinstance(clause, dict):
            raise DocumentUpdateError("Expected a query clause document")
        for path, condition in clause.items():
            if path == "$and":
                if not isinstance(condition, list):
                    raise DocumentUpdateError("$and requires query clauses")
                for item in condition:
                    gather(item)
            elif path.startswith("$"):
                continue
            elif isinstance(condition, dict) and any(key.startswith("$") for key in condition):
                if set(condition) == {"$eq"}:
                    assign(path, condition["$eq"])
            else:
                assign(path, condition)
    def assign(path, value):
        _path(path)
        if path in assignments and not bson_equal(assignments[path], value):
            raise DocumentUpdateError("Conflicting upsert equality predicates")
        assignments[path] = value
    gather(query)
    _paths_unique(assignments)
    result = {}
    for path, value in assignments.items():
        _set(result, path, value)
    return result
