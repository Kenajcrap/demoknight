import re
import vdf

try:
    from collections.abc import Mapping
except:
    from collections import Mapping


def patched_parse(fp, mapper=dict, merge_duplicate_keys=True, escaped=True):
    """
    Deserialize ``s`` (a ``str`` or ``unicode`` instance containing a VDF)
    to a Python object.
    ``mapper`` specifies the Python object used after deserializetion. ``dict` is
    used by default. Alternatively, ``collections.OrderedDict`` can be used if you
    wish to preserve key order. Or any object that acts like a ``dict``.
    ``merge_duplicate_keys`` when ``True`` will merge multiple KeyValue lists with the
    same key into one instead of overwriting. You can se this to ``False`` if you are
    using ``VDFDict`` and need to preserve the duplicates.
    """
    if not issubclass(mapper, Mapping):
        raise TypeError("Expected mapper to be subclass of dict, got %s" % type(mapper))
    if not hasattr(fp, "readline"):
        raise TypeError(
            "Expected fp to be a file-like object supporting line iteration"
        )

    stack = [mapper()]
    expect_bracket = False

    re_keyvalue = re.compile(
        r'^("(?P<qkey>(?:\\.|[^\\"])*)"|(?P<key>#?[a-z0-9\-\_\\\?\+$%<>]+))'
        r"([ \t]*("
        r'"(?P<qval>(?:\\.|[^\\"])*)(?P<vq_end>")?'
        r"|(?P<val>(?:(?<!/)/(?!/)|[a-z0-9\-\_\\\?\*\.\|$<> ])+)"
        r"|(?P<sblock>{[ \t]*)(?P<eblock>})?"
        r"))?",
        flags=re.I,
    )

    for lineno, line in enumerate(fp, 1):
        if lineno == 1:
            line = vdf.strip_bom(line)

        line = line.lstrip()

        # skip empty and comment lines
        if line == "" or line[0] == "/":
            continue

        # one level deeper
        if line[0] == "{":
            expect_bracket = False
            continue

        if expect_bracket:
            raise SyntaxError(
                "vdf.parse: expected openning bracket",
                (getattr(fp, "name", "<%s>" % fp.__class__.__name__), lineno, 1, line),
            )

        # one level back
        if line[0] == "}":
            if len(stack) > 1:
                stack.pop()
                continue

            raise SyntaxError(
                "vdf.parse: one too many closing parenthasis",
                (getattr(fp, "name", "<%s>" % fp.__class__.__name__), lineno, 0, line),
            )

        # parse keyvalue pairs
        while True:
            match = re_keyvalue.match(line)

            if not match:
                try:
                    line += next(fp)
                    continue
                except StopIteration:
                    raise SyntaxError(
                        "vdf.parse: unexpected EOF (open key quote?)",
                        (
                            getattr(fp, "name", "<%s>" % fp.__class__.__name__),
                            lineno,
                            0,
                            line,
                        ),
                    )

            key = (
                match.group("key")
                if match.group("qkey") is None
                else match.group("qkey")
            )
            val = match.group("qval")
            if val is None:
                val = match.group("val")
                if val is not None:
                    val = val.rstrip()
                    if val == "":
                        val = None

            if escaped:
                key = vdf._unescape(key)

            # we have a key with value in parenthesis, so we make a new dict obj (level deeper)
            if val is None:
                if merge_duplicate_keys and key in stack[-1]:
                    _m = stack[-1][key]
                    # we've descended a level deeper, if value is str, we have to overwrite it to mapper
                    if not isinstance(_m, mapper):
                        _m = stack[-1][key] = mapper()
                else:
                    _m = mapper()
                    stack[-1][key] = _m

                if match.group("eblock") is None:
                    # only expect a bracket if it's not already closed or on the same line
                    stack.append(_m)
                    if match.group("sblock") is None:
                        expect_bracket = True

            # we've matched a simple keyvalue pair, map it to the last dict obj in the stack
            else:
                # if the value is line consume one more line and try to match again,
                # until we get the KeyValue pair
                if match.group("vq_end") is None and match.group("qval") is not None:
                    try:
                        line += next(fp)
                        continue
                    except StopIteration:
                        raise SyntaxError(
                            "vdf.parse: unexpected EOF (open quote for value?)",
                            (
                                getattr(fp, "name", "<%s>" % fp.__class__.__name__),
                                lineno,
                                0,
                                line,
                            ),
                        )

                stack[-1][key] = vdf._unescape(val) if escaped else val

            # exit the loop
            break

    if len(stack) != 1:
        raise SyntaxError(
            "vdf.parse: unclosed parenthasis or quotes (EOF)",
            (getattr(fp, "name", "<%s>" % fp.__class__.__name__), lineno, 0, line),
        )

    return stack.pop()


vdf.parse = patched_parse
