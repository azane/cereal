from typing import *
import inspect
from enum import Enum
from contextlib import contextmanager
import json
import numpy as np


class CerealError(TypeError):
    pass


class CerealEncoder(json.JSONEncoder):
    # noinspection PyProtectedMember
    def default(self, obj):
        if isinstance(obj, Cereal):
            return obj.to_json()
        elif isinstance(obj, Enum):
            return obj.name
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        # TODO does numpy have a predefined collection of all these stringable
        #  types?
        elif type(obj) in [np.int64, np.int32]:
            return int(obj)
        elif type(obj) in [np.float64, np.float32]:
            return float(obj)
        else:
            return json.JSONEncoder.default(self, obj)


class Cereal:
    """
    This superclass provides support for children to be (de)serializeable, like so:
    >>> class CerealChild(Cereal):
    >>>     ...
    >>>
    >>> # Instantiated in pure python.
    >>> cereal_child = CerealChild(...)
    >>> # Serialize.
    >>> cereal_child_json = json.dumps(cereal_child, cls=CerealEncoder)
    >>> # Deserialize.
    >>> cereal_child_2 = CerealChild(**json.loads(cereal_child_json))

    If a child of Cereal has other objects as attributes, they should also inherit from Cereal. By explicitly defining
     inputs in the __init__ of a Cereal class and type-hinting them as either CerealChild, List[CerealChild],
     or Optional[CerealChild], decorating the __init__ with @Cereal.auto_deserialize_nested will make the __init__
     automatically convert raw json into the type-hinted entities. This only happens automatically for those supported
     types. Otherwise, you'll have to detect and convert raw json into entities after super().__init__(**kwargs).
    See the __name__ == "__main__" block, in this file, for an example.

    Children of Cereal must always take **kwargs and pass **kwargs to super().__init__(**kwargs). This allows for meta
     data to be passed.

    Attribute definitions can happen in the __init__ signature as explicit arguments or by initializing optional
     attributes before super().__init__(**kwargs). If defined attributes are not found in **kwargs, they will be
     listed as missing_properties. Attributes present in **kwargs but not defined before super().__init__(**kwargs)
     will be listed as extra_properties. If an attribute is defined after deserialization, it will be removed from
     extra_properties and missing_properties.

    Abstracting accessors away from the input json should be done either via @property methods or by initializing
     some other field after super().__init__(**kwargs). The former will not reserialize, while the
     latter will. If you don't want a non @property attributes to reserialize, you have to override to_json and
     manually remove it. TODO provide support to not serialize cached non @property attributes.

    For examples, see the __name__ == "__main__" section in this file
    """

    CEREAL_META = "_cereal_meta"

    _MISSING_PROPERTIES = "missing_properties"
    _EXTRA_PROPERTIES = "extra_properties"
    _CEREAL_TYPE = "cereal_type"

    OMIT_NULL_IN_SERIAL = True

    @contextmanager
    def auto_deserialize_initialized_context(self):
        """
        A kind of hacky helper function that automatically deserializes attributes that are
         defaulted to Cereal entities before it. Used like so:
        >>> with self.auto_deserialize_initialized_context():
        >>>     super().__init__(**kwargs)
        """
        todeserialize = dict()
        for k, v in self.__dict__.items():
            if isinstance(v, Cereal):
                todeserialize[k] = type(v)

        yield  # super().__init__(**kwargs) will happen here.

        for k, t in todeserialize.items():
            v = self.__dict__[k]
            if type(v) is not t:
                assert isinstance(v, dict), "Nested cereal entity was overwritten with a non-dictionary."
                self.__dict__[k] = t(**v)
    
    def check_nested_arguments(self):
        """
        An overrideable method that can check nested arguments after deserialization. This is useful for
         checking that nested arguments are valid. This gets called after auto_deserialize_hinted_nested.
        This is necessary because when deserializing, the nested Cereal entities are still in dict form, and are
         until after the auto_deserialize_hinted_nested decorator fires.
        """
        assert True
    
    @staticmethod
    def try_deserialize_cereal_type(v: Dict, *unwrapped_hints: Type['Cereal']) -> Union['Cereal', Dict]:
        cereal_type = v.get(Cereal.CEREAL_META, {}).get(Cereal._CEREAL_TYPE)
        if cereal_type is not None:
            for t in unwrapped_hints:
                if inspect.isclass(t) and issubclass(t, Cereal) and t.__name__ == cereal_type:
                    return t(**v)
            raise CerealError(f"Cereal type {cereal_type} was defined in json, but type hint(s) "
                              f"did not match that type.")
        else:
            # If this isn't a cereal type just return the raw dictionary.
            # TODO r20njldf support cases where a cereal entity is inside a dict (as key or val).
            #  Without this, dict's are assumed to comprise only natively deserializeble types.
            # Could do this by just doing a dictionary comprehension and recursing deeper...but
            #  would need the generic key/val types from that dictionary...
            return v
    
    @staticmethod
    def recursive_auto_deserialize_hinted_nested(v, unwrapped_hint) -> object:
        uw = unwrapped_hint

        # Unwrap Unions.
        if hasattr(uw, "__origin__") and uw.__origin__ is Union:
            uwa = uw.__args__

            # Unwrap optionals and recurse.
            if len(uwa) == 2 and uwa[0] is not type(None) and uwa[1] is type(None):
                return Cereal.recursive_auto_deserialize_hinted_nested(v, uwa[0])
            
            # Handle unions over cereal types.
            if isinstance(v, dict):
                return Cereal.try_deserialize_cereal_type(v, *uwa)
            
        # Unwrap lists.
        elif isinstance(v, list):
            # If this is supposed to be a list, recursevely handle each member.
            if hasattr(uw, "__origin__") and uw.__origin__ is list:
                uwa = uw.__args__
                if len(uwa) != 1:
                    raise CerealError("Type hinting lists without a single inner type is not allowed.")
                return [Cereal.recursive_auto_deserialize_hinted_nested(i, uwa[0]) for i in v]
            # If it's supposed to be a numpy array, just return that.
            elif inspect.isclass(uw) and issubclass(uw, np.ndarray):
                return np.array(v)
            
        # TODO unwrap tuples

        # TODO unwrap dicts r20njldf
        
        # Handle enum types defined as strings.
        elif isinstance(v, str) and inspect.isclass(uw) and issubclass(uw, Enum):
            return uw[v]

        # If, at any point, we get a dict, and we've made it past all the unwraps above,
        #  assume it's a cereal type. If it isn't, we'll jsut get the raw dict back.
        # TODO r20njldf support cases where a cereal entity is inside a dict (as key or val).
        #  Without this, dict's are assumed to comprise only natively deserializeble types.
        elif isinstance(v, dict):
            # Unwrap constrained type vars (cereal constrained generics).
            if isinstance(uw, TypeVar) and len(uw.__constraints__):
                return Cereal.try_deserialize_cereal_type(v, *uw.__constraints__)
            # Decouple generic cereal types from their generics.
            # It's fine to ignore the generic types here, because if those generic
            #  types will be defined simply in virtue of them being present as object
            #  attributes.
            elif hasattr(uw, "__origin__") and issubclass(uw.__origin__, Cereal):
                return Cereal.try_deserialize_cereal_type(v, uw.__origin__)
            else:
                return Cereal.try_deserialize_cereal_type(v, uw)
        
        # If, after all that, we get here, it's probably just a primitive type that doesn't need
        #  to be objectified further.
        return v
    
    @staticmethod
    def auto_deserialize_hinted_nested(__init__):
        """
        A decorator providing automatic deserialization of nested Cereal entities. Decorate the initializer
        of Cereal child classes and type hint arguments with either CerealChild, List[CerealChild], or
        Optional[CerealChild], where the last should take a default of None.
        """

        def wrap(self, *args, **kwargs):
            # Get the initializer type hints.
            hints = get_type_hints(__init__)
            # Execute the initializer.
            __init__(self, *args, **kwargs)

            # TODO provide support for general type-hinted generics with inner Cereal types. E.g. Dict[str, Cereal]
            # TODO there's probably an elegant way to this.
            
            # Run through all the attributes and send kwargs to constructors where necessary.
            for k, v in self.__dict__.items():
                if k in hints:
                    self.__dict__[k] = Cereal.recursive_auto_deserialize_hinted_nested(v, hints[k])
            
            self.check_nested_arguments()

        return wrap

    def __init__(self, **kwargs):
        super().__init__()

        # Initialize some meta information.
        self.__dict__[self.CEREAL_META] =\
            {
                self._MISSING_PROPERTIES: [],
                self._EXTRA_PROPERTIES: [],
                self._CEREAL_TYPE: type(self).__name__
            }

        self._load_check(d=kwargs)

        # This is computed in load check, so don't take whatever was in the raw.
        if self.CEREAL_META in kwargs.keys():
            kwargs.pop(self.CEREAL_META)

        self._update(**kwargs)

    def __deepcopy__(self, memodict=None):
        # Not the fastest deep copy, but fast to implement...
        raw_self = json.dumps(self, cls=CerealEncoder)
        return type(self)(**json.loads(raw_self))

    def __setattr__(self, key, value):

        # Making sure this key exists prevents us from updating lists before they've been created.
        if self.CEREAL_META in self.__dict__:
            # Remove from both lists, as this attribute has now been defined, so it's neither extra nor missing.
            try:
                self.missing_properties.remove(key)
            except ValueError:
                pass
            try:
                self.extra_properties.remove(key)
            except ValueError:
                pass

        super().__setattr__(key, value)

    @property
    def missing_properties(self) -> List[str]:
        """
        Returns the set of properties that were defined in the class prior to deserialization, but not present in the
        raw serializied json and not added since then. These are necessarily optional parameters.
        """
        return self.__dict__[self.CEREAL_META][self._MISSING_PROPERTIES]

    @property
    def extra_properties(self) -> List[str]:
        """
        Returns the set of properties that were not defined in the class prior to deserialization, but present in the
        raw serialized json and not set as an attribute, in code, since then.
        """
        return self.__dict__[self.CEREAL_META][self._EXTRA_PROPERTIES]

    def _update(self, **kwargs):
        self.__dict__.update(kwargs)

    def _load_check(self, d: Dict):
        defd_attribs = set(self.__dict__.keys())
        pass_attribs = set([k for k, v in d.items() if v is not None])

        # Some attributes may have been defined explicitly, so treat those as passed.
        pass_attribs.update([k for k in defd_attribs if self.__dict__[k] is not None])

        extra = pass_attribs - defd_attribs
        self.extra_properties.extend(sorted(extra))

        missing = defd_attribs - pass_attribs - {self.CEREAL_META}
        self.missing_properties.extend(sorted(missing))

    def to_json(self):
        # Overwrite this if custom functionality is desired beyond the
        #  fact that the json library will recurse through this.
        # i.e. classes that inherit from Cereal, even if nested, don't
        #  need anything extra.
        # return self.__dict__
        if self.OMIT_NULL_IN_SERIAL:
            return {k: v for k, v in self.__dict__.items() if v is not None}
        else:
            return self.__dict__
    
    def __eq__(self, other):
        if not issubclass(type(other), Cereal):
            raise NotImplementedError("Equality between a Cereal class and a non-Cereal class is not supported.")
        # Again, not the fastest but :shrug:
        this_str = json.dumps(self, cls=CerealEncoder, sort_keys=True)
        other_str = json.dumps(other, cls=CerealEncoder, sort_keys=True)
        return this_str == other_str


if __name__ == "__main__":
    # TODO put in broken out unit test.
    class Inner(Cereal):
        def __init__(self, value: int, **kwargs):
            self.value = value
            super().__init__(**kwargs)


    from enum import auto


    class TestEnum(Enum):
        thing1 = auto()
        thing2 = auto()


    class Outer(Cereal):
        # Decorate to take advantage of auto-deserialization features.
        @Cereal.auto_deserialize_hinted_nested
        def __init__(self, inner1: Inner, inners: List[Inner], value: int, inner2: Optional[Inner] = None,
                     thing: Optional[TestEnum] = None, **kwargs):
            self.inner1 = inner1
            self.inner2 = inner2
            self.inners = inners
            self.value = value
            self.thing = thing
            self.value2 = None  # type: Optional[float]
            self.inner3 = None  # type: Optional[Inner]

            # TODO support properties better, but this precise pattern works for now.
            # Type default value.
            self._bval: Optional[bool]
            # Set passed value
            self.bval = kwargs.pop('bval', kwargs.pop('_bval', None))

            super().__init__(**kwargs)

            # Not type-hinted in signature, but not a primitive type, so need to initialize when deserializing, but
            #  only then and not when initializing from Python with an actual entity.
            if isinstance(self.inner3, dict):
                self.inner3 = Inner(**self.inner3)

        @property
        def bval(self) -> Optional[bool]:
            return self._bval

        @bval.setter
        def bval(self, value: Optional[bool]):
            self._bval = value

    i1 = Inner(1)
    i2 = Inner(2)
    i3 = Inner(3)
    o = Outer(i1, [i1, i2], 3, i2, inner3=i3, value2=3.2, thing=TestEnum.thing1)
    d2 = json.dumps(o, cls=CerealEncoder, indent=4)
    o2 = Outer(**json.loads(d2))
    assert json.dumps(o, cls=CerealEncoder) == json.dumps(o2, cls=CerealEncoder)

    o.bval = True
    assert Outer(**json.loads(json.dumps(o, cls=CerealEncoder, indent=4))).bval is True
    assert o.bval is True
    assert o._bval is True
    o.bval = False
    assert Outer(**json.loads(json.dumps(o, cls=CerealEncoder, indent=4))).bval is False
    assert o.bval is False
    assert o._bval is False
    o.bval = None
    assert Outer(**json.loads(json.dumps(o, cls=CerealEncoder, indent=4))).bval is None
    assert o.bval is None
    assert o._bval is None

    d3 = json.loads(d2)
    d3["extra_value"] = 10.
    del d3["inner2"]
    o3 = Outer(**d3)
    assert o3.inner2 is None
    assert o3.extra_properties == ['extra_value']
    assert set(o3.missing_properties) == {'inner2', '_bval'}
    o3.inner2 = i2
    o3.bval = False
    assert not len(o3.missing_properties)
    o3.extra_value = 3.
    assert not len(o3.extra_properties)
