"""
Parameter handling, each instance supports,
- converting to XML for sending to the FE
- conversion into a Pydantic field for validating on the BE during an RPC call

# TODO - fix generics
"""
from __future__ import annotations

import abc
import base64
import io
import json
import math
import tempfile
import typing as t
from datetime import date, datetime, time
from pathlib import Path

import pydantic as pyd
from lxml.builder import ElementMaker
from lxml.etree import _Element as Element

from datapane.client.exceptions import DPClientError
from datapane.common import SDict, SList
from datapane.common.viewxml_utils import mk_attribs

E = ElementMaker()
X = t.TypeVar("X")
Field = t.Tuple[t.Type, t.Any]
ValidatorF = t.Callable[[X], X]

Numeric = t.Union[float, int]


class Parameter(abc.ABC, t.Generic[X]):
    _T: type[X]
    _tag: str
    cacheable: bool = True

    def __init__(self, name: str, label: t.Optional[str], initial: t.Optional[X], *, allow_empty: bool = False):
        if not name:
            raise DPClientError(f"A non empty name must be provided, got '{name}'")
        if label == "":
            raise DPClientError("label must be a non-empty string or None")
        self.name = name
        self.label = label
        self.initial = initial

        initial = self._proc_initial(self.initial) if self.initial is not None else None
        self.attribs: SDict = dict(name=self.name, label=self.label, required=not allow_empty, initial=initial)

    def _check_instance(self):
        """
        Perform basic checks that the Parameter was constructed correctly.
        """
        # This method throws a fairly clear error if user passes bad strings,
        # which we want to catch as soon as possible.
        self._to_xml()

    def _proc_initial(self, x: X) -> t.Any:
        return x

    def _as_field(self) -> Field:
        return (self._T, self.initial)

    def _validator(self) -> ValidatorF:
        # return identity
        return lambda x: x

    def _to_xml(self) -> Element:
        return getattr(E, self._tag)(**self._attribs)

    @property
    def _attribs(self) -> SDict:
        return mk_attribs(**self.attribs)


class Switch(Parameter[bool]):
    _T = bool
    _tag = "Switch"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: bool = False,
    ):
        super().__init__(name, label, initial)
        self._check_instance()


class TextBox(Parameter[str]):
    _T = str
    _tag = "TextBox"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: str = "",
        allow_empty: bool = False,
    ):
        super().__init__(name, label, initial, allow_empty=allow_empty)
        self._check_instance()


class NumberBox(Parameter[float]):
    _T = float
    _tag = "NumberBox"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: float,
    ):
        super().__init__(name, label, initial)
        self._check_instance()


class Range(Parameter):
    _T = float
    _tag = "Range"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: Numeric,
        min: Numeric,
        max: Numeric,
        step: t.Optional[Numeric] = None,
    ):
        super().__init__(name, label, self._T(initial))
        if any(isinstance(v, float) and (math.isinf(v) or math.isnan(v)) for v in (min, max, step)):
            raise DPClientError("min/max/step must not be `inf` or `nan`")
        self.min = self._T(min)
        self.max = self._T(max)
        self.step = None if step is None else self._T(step)
        self._check_instance()

    def _as_field(self) -> Field:
        return (pyd.confloat(ge=self.min, le=self.max), self.initial)

    def _to_xml(self) -> Element:
        attribs = mk_attribs(**self.attribs, min=self.min, max=self.max, step=self.step)
        return E.Range(**attribs)


class Choice(Parameter[str]):
    """Choose a single element from a set"""

    _T = str
    _tag = "Choice"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        options: SList,
        initial: t.Optional[str] = None,
    ):
        # valid params
        if not options:
            raise DPClientError("At least one option must be provided")
        if initial is not None and initial not in options:
            raise DPClientError(f"Initial value `{initial}` must be present in the options")
        if any(opt == "" for opt in options):
            raise DPClientError("All options must be non-empty strings")
        super().__init__(name, label, initial)
        self.options = options
        self._check_instance()

    def _validator(self) -> ValidatorF[str]:
        def f(x: str):
            assert x in self.options, "not in options"
            return x

        return f

    def _to_xml(self) -> Element:
        attribs = mk_attribs(**self.attribs, choices=json.dumps(self.options))
        return E.Choice(**attribs)


class MultiChoice(Parameter[SList]):
    """Choose multiple elements from a set"""

    _T = SList
    _tag = "MultiChoice"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: SList = [],
        options: SList,
        allow_empty: bool = False,
    ):
        # valid params
        initial = initial or []
        if not options:
            raise DPClientError("At least one option must be provided")
        if any(d not in options for d in initial):
            raise DPClientError(f"All items in default value `{initial}` must be present in the options")
        if any(opt == "" for opt in options):
            raise DPClientError("All options must be non-empty strings")
        super().__init__(name, label, initial, allow_empty=allow_empty)
        self.options = options
        self._check_instance()

    def _check(self, xs: SList, ys: SList):
        return set(xs).issubset(ys)

    def _validator(self) -> ValidatorF[SList]:
        def f(xs: SList):
            assert self._check(xs, self.options), "not in options"
            return xs

        return f

    def _proc_initial(self, x: X) -> t.Any:
        return json.dumps(x)

    def _to_xml(self) -> Element:
        attribs = mk_attribs(**self.attribs, choices=json.dumps(self.options))
        return E.MultiChoice(**attribs)


class Tags(Parameter[SList]):
    """Create a list of strings"""

    _T = SList
    _tag = "Tags"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: SList = [],
        allow_empty: bool = False,
    ):
        super().__init__(name, label, initial or [], allow_empty=allow_empty)
        self._check_instance()

    def _proc_initial(self, x: X) -> t.Any:
        return json.dumps(x)


class Date(Parameter[date]):
    _T = date
    _tag = "Date"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: t.Optional[date] = None,
    ):
        super().__init__(name, label, initial)
        self._check_instance()

    def _proc_initial(self, x: date) -> t.Any:
        return x.isoformat()


class Time(Parameter[time]):
    _T = time
    _tag = "Time"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: t.Optional[time] = None,
    ):
        super().__init__(name, label, initial)
        self._check_instance()

    def _proc_initial(self, x: time) -> t.Any:
        return x.isoformat()


class DateTime(Parameter[datetime]):
    _T = datetime
    _tag = "DateTime"

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        *,
        initial: t.Optional[datetime] = None,
    ):
        super().__init__(name, label, initial)
        self._check_instance()

    def _proc_initial(self, x: datetime) -> t.Any:
        return x.isoformat()


class B64Path(Path):
    """Pydantic custom type based upon Path object"""

    # Hack to deal with Path not being directly subclassable (fixed in Py3.12)
    _flavour = type(Path())._flavour

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: str):
        """Decode and save the b64 string to a temp file on disk"""
        f_v = io.StringIO(v)
        with tempfile.NamedTemporaryFile("wb", delete=False, prefix="dp-uploaded-") as out_f:
            base64.decode(f_v, out_f)
        return cls(out_f.name)


class File(Parameter[B64Path]):
    _T = t.Optional[B64Path]
    _tag = "File"
    # NOTE - currently cacheable controls will make an interactive block as non-cacheable
    # we could use sha of the file eventually
    cacheable = False

    def __init__(
        self,
        name: str,
        label: t.Optional[str] = None,
        initial: t.Optional[B64Path] = None,
        allow_empty: bool = False,
    ):
        # Set default to None to mark an optional File
        super().__init__(name, label, initial, allow_empty=allow_empty)
        self._check_instance()

    def _validator(self) -> ValidatorF:
        # bit hacky, but create a Path object from the internal B64Path object
        def f(x: B64Path):
            assert x.is_file() and x.exists()
            return Path(x)

        return f
