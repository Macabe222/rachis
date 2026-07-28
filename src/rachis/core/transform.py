# ----------------------------------------------------------------------------
# Copyright (c) 2016-2026, QIIME 2 development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file LICENSE, distributed with this software.
# ----------------------------------------------------------------------------
from __future__ import annotations

from enum import Enum
import pathlib

from rachis import sdk
from rachis.plugin import model
from rachis.core import util


def identity_transformer(view):
    return view


class ModelType:
    @staticmethod
    def from_view_type(view_type):
        if issubclass(view_type, model.base.FormatBase):
            if issubclass(view_type,
                          model.SingleFileDirectoryFormatBase):
                # HACK: this is necessary because we need to be able to "act"
                # like a FileFormat when looking up transformers, but our
                # input/output coercion still needs to bridge the
                # transformation as we do not have transitivity

                # In other words we have DX and we have transformers of X
                # In a perfect world we would automatically define DX -> X and
                # let transitivity handle it, but since that doesn't exist, we
                # need to treat DX as if it were X and coerce behind the scenes

                # TODO: redo this when transformers are transitive
                return SingleFileDirectoryFormatType(view_type)
            # Normal format type
            return FormatType(view_type)
        else:
            # TODO: supporting stdlib.typing may require an alternate
            # model type as `isinstance` is a meaningless operation
            # for them so validation would need to be handled differently
            return ObjectType(view_type)

    def __init__(self, view_type):
        self._pm = sdk.PluginManager()
        self._view_type = view_type
        self._view_name = util.get_view_name(self._view_type)
        self._record = None

        if self._view_name in self._pm.views:
            self._record = self._pm.views[self._view_name]

    def make_transformation(self, other, recorder=None):
        target_node = find_transformation_path(
            self._view_type, other._view_type
        )

        if target_node is None:
            raise Exception("No transformation from %r to %r" %
                            (self._view_type, other._view_type))

        if recorder is not None:
            recorder(
                target_node.record,
                input_name=self._view_name,
                input_record=self._record,
                output_name=other._view_name,
                output_record=other._record
            )

        return compose_transformation(target_node)

    def _get_transformer_to(self, other):
        transformer, record = self._lookup_transformer(self._view_type,
                                                       other._view_type)
        if transformer is None:
            return other._get_transformer_from(self)

        return transformer, record

    def has_transformation(self, other):
        """ Checks to see if there exist transformers for other

        Parameters
        ----------
        other : ModelType subclass
           The object being checked for transformer

        Returns
        -------
        bool
            Does the specified transformer exist for other?
        """

        transformer, _ = self._get_transformer_to(other)
        return transformer is not None

    def _get_transformer_from(self, other):
        return None, None

    def coerce_view(self, view):
        return view

    def _lookup_transformer(self, from_, to_):
        if from_ == to_:
            return identity_transformer, None

        search_node = find_transformation_path(from_, to_)
        if search_node is None or search_node.record is None:
            return None, None
        return search_node.record.transformer, search_node.record

    def set_user_owned(self, view, value):
        pass


class FormatType(ModelType):
    def coerce_view(self, view):
        if type(view) is str or isinstance(view, pathlib.Path):
            return self._view_type(view, mode='r')

        if isinstance(view, self._view_type):
            # wrap original path (inheriting the lifetime) and return a
            # read-only instance
            return self._view_type(view.path, mode='r')

        return view

    def validate(self, view, level='min'):
        if not isinstance(view, self._view_type):
            raise TypeError("%r is not an instance of %r."
                            % (view, self._view_type))
        # Formats have a validate method, so defer to it
        view.validate(level)

    def set_user_owned(self, view, value):
        view.path._user_owned = value


class SingleFileDirectoryFormatType(FormatType):
    def __init__(self, view_type):
        # Single file directory formats have only one file named `file`
        # allowing us construct a model type from the format of `file`
        self._wrapped_view_type = view_type.file.format
        super().__init__(view_type)

    def _get_transformer_to(self, other):
        # Legend:
        # - Dx: single directory format of x
        # - Dy: single directory format of y
        # - x: input format x
        # - y: output format y
        # - ->: implicit transformer
        # - =>: registered transformer
        # - :> final transformation
        # - |: or, used when multiple situation are possible

        # It looks like all permutations because it is...

        # Dx :> y | Dy via Dx => y | Dy
        transformer, record = self._wrap_transformer(self, other)
        if transformer is not None:
            return transformer, record

        # Dx :> Dy via Dx -> x => y | Dy
        transformer, record = self._wrap_transformer(self, other,
                                                     wrap_input=True)
        if transformer is not None:
            return transformer, record

        if type(other) is type(self):
            # Dx :> Dy via Dx -> x => y -> Dy
            transformer, record = self._wrap_transformer(
                self, other, wrap_input=True, wrap_output=True)
            if transformer is not None:
                return transformer, record

        # Out of options, try for Dx :> Dy via Dx => y -> Dy
        return other._get_transformer_from(self)  # record is included

    def _get_transformer_from(self, other):
        # x | Dx :> Dy via x | Dx => y -> Dy
        # IMPORTANT: reverse other and self, this method is like __radd__
        return self._wrap_transformer(other, self, wrap_output=True)

    def _wrap_transformer(self, in_, out_, wrap_input=False,
                          wrap_output=False):
        input = in_._wrapped_view_type if wrap_input else in_._view_type
        output = out_._wrapped_view_type if wrap_output else out_._view_type

        transformer, record = self._lookup_transformer(input, output)
        if transformer is None:
            return None, None

        if wrap_input:
            transformer = in_._wrap_input(transformer)

        if wrap_output:
            transformer = out_._wrap_output(transformer)

        return transformer, record

    def _wrap_input(self, transformer):
        def wrapped(view):
            return transformer(view.file.view(self._wrapped_view_type))

        return wrapped

    def _wrap_output(self, transformer):
        def wrapped(view):
            new_view = self._view_type()
            file_view = transformer(view)
            if transformer is not identity_transformer:
                self.set_user_owned(file_view, False)
            new_view.file.write_data(file_view, self._wrapped_view_type)
            return new_view

        return wrapped


class ObjectType(ModelType):
    def validate(self, view, level=None):
        if not isinstance(view, self._view_type):
            raise TypeError("%r is not of type %r, cannot transform further."
                            % (view, self._view_type))


class TransformType(Enum):
    registered = 1
    wrap = 2
    unwrap = 3


class SearchNode:
    def __init__(
        self,
        type_: type,
        parent: SearchNode | None,
        record = None,
        transform_type: TransformType = TransformType.registered,
        steps = 0
    ):
        self.type_ = type_
        self.parent = parent
        self.record = record
        self.transform_type = transform_type
        self.steps = steps

    def __eq__(self, other):
        return self.type_ == other.type_

    def __hash__(self):
        return hash(self.type_)

    def __repr__(self):
        return (
            f'SearchNode(id={id(self)}, type_={repr(self.type_)}, '
            f'parent={None if self.parent is None else id(self.parent)}, '
            f'transform_type={self.transform_type})'
        )


def find_transformation_path(start: type, target: type) -> SearchNode | None:
    '''
    Searches for a transformation path from `start` to `target`. The path is
    encoded in the chain of parents of the returned SearchNode.

    Parameters
    ----------
    start : type
        The type we wish to transform from.
    target : type
        The type we wish to transform to.

    Returns
    -------
    SearchNode | None
        A SearchNode of the target type, if reachable, otherwise None.
    '''
    pm = sdk.PluginManager()

    visited: set[SearchNode] = set()
    current = SearchNode(type_=start, parent=None)
    outstanding: list[SearchNode] = [current]
    while outstanding != []:
        current = outstanding.pop()

        if current in visited:
            continue
        else:
            visited.add(current)

        if current.type_ == target:
            return current

        for neighbor, transform_record in pm.transformers.get(
            current.type_, {}
        ).items():
            if transform_record.upgrade or current.steps == 0:
                outstanding.insert(
                    0, SearchNode(
                        type_=neighbor,
                        parent=current,
                        record=transform_record,
                        steps=current.steps+1
                    )
                )

        if issubclass(current.type_, model.base.FormatBase):
            # add synthetic link for Dx -> x
            if issubclass(current.type_, model.SingleFileDirectoryFormatBase):
                neighbor = SearchNode(
                    type_=current.type_.file.format,
                    parent=current,
                    record=current.record,
                    transform_type=TransformType.unwrap,
                    steps=current.steps
                )
                outstanding.insert(0, neighbor)

            # add synthetic link(s) x -> Dx
            else:
                for sfdf in pm._ff_to_sfdf.get(current.type_, []):
                    neighbor = SearchNode(
                        type_=sfdf,
                        parent=current,
                        record=current.record,
                        transform_type=TransformType.wrap,
                        steps=current.steps
                    )
                    outstanding.insert(0, neighbor)

    return None


def compose_transformation(target: SearchNode | None):
    if target is None:
        return None

    pm = sdk.PluginManager()

    steps = []
    current = target
    while current is not None:
        steps.insert(0, current)
        current = current.parent

    if len(steps) == 1:
        def identity_transformation(view, validate_level='min'):
            from_mt = ModelType.from_view_type(steps[0].type_)
            current = from_mt.coerce_view(view)
            from_mt.validate(current, level=validate_level)

            return current

        return identity_transformation

    def transformation(view, validate_level='min'):
        current = view
        for i in range(len(steps) - 1):
            from_type = steps[i].type_
            to_type = steps[i + 1].type_

            if steps[i + 1].transform_type == TransformType.wrap:
                transformer = wrap_transformer(from_type, to_type)
            elif steps[i + 1].transform_type == TransformType.unwrap:
                transformer = unwrap_transformer(from_type)
            else:
                transformer = pm.transformers[from_type][to_type].transformer

            from_mt = ModelType.from_view_type(from_type)
            to_mt = ModelType.from_view_type(to_type)

            current = from_mt.coerce_view(current)
            from_mt.validate(current, level=validate_level)
            current = transformer(current)

            current = to_mt.coerce_view(current)
            to_mt.validate(current, level=validate_level)
            to_mt.set_user_owned(current, False)

        return current

    return transformation


def wrap_transformer(file_type: type, sfdf_type: type):
    '''
    A transformer used to convert any `FileFormat` into its associated
    `SingleFileDirectoryFormat`.
    '''
    def transformer(view):
        sfdf = sfdf_type()
        sfdf.file.write_data(view, file_type)
        return sfdf

    return transformer


def unwrap_transformer(sfdf_type: type):
    '''
    A transformer used to convert any `SingleFileDirectoryFormat` into the
    contained `FileFormat`.
    '''
    file_type = sfdf_type.file.format

    def transformer(view):
        return view.file.view(file_type)

    return transformer
