import inspect

from django_fsm import can_proceed, has_transition_perm
from django.utils.translation import gettext_lazy as _

from rest_framework import exceptions
from rest_framework.decorators import action
from rest_framework.response import Response


def get_transition_viewset_method(transition_name):
    """Create a viewset method for the provided `transition_name` """

    def transition_action(self, request, *args, **kwargs):
        instance = self.get_object()
        transition_method = getattr(instance, transition_name)

        if not can_proceed(transition_method, self.request.user):
            raise exceptions.ValidationError({'detail': _('Conditions not met')})

        if not has_transition_perm(transition_method, self.request.user):
            raise exceptions.PermissionDenied

        if transition_name in self.excluded_transitions:
            raise exceptions.PermissionDenied

        if hasattr(self, 'get_{0}_kwargs'.format(transition_name)):
            transition_kwargs = getattr(self, 'get_{0}_kwargs'.format(transition_name))()
        else:
            transition_kwargs = {}

        signature = inspect.signature(transition_method)

        if 'by' in signature.parameters and 'by' not in transition_kwargs:
            transition_kwargs['by'] = self.request.user

        if 'request' in signature.parameters and 'request' not in transition_kwargs:
            transition_kwargs['request'] = self.request

        result = transition_method(**transition_kwargs)

        if self.save_after_transition:
            instance.save()

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        if transition_name in self.return_result_of:
            return Response(result)

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    transition_action.__name__ = transition_name
    try:
        transition_action.mapping = dict.fromkeys(transition_action.mapping, transition_name)
    except AttributeError:
        pass
    return transition_action


def get_drf_fsm_mixin(Model, fieldname='state'):
    """
    Find all transitions defined on `Model`, then create a corresponding
    viewset action method for each and apply it to `Mixin`. Finally, return
    `Mixin`
    """

    class Mixin(object):
        save_after_transition = True
        return_result_of = []
        excluded_transitions = []

        @action(methods=['GET'], detail=True, url_name='possible-transitions', url_path='possible-transitions')
        def possible_transitions(self, request, *args, **kwargs):
            instance = self.get_object()
            return Response(
                {
                    'transitions': [
                        trans.name.replace('_', '-')
                        for trans in getattr(instance, 'get_available_{}_transitions'.format(fieldname))()
                        if trans.has_perm(instance, request.user) and (trans.name not in self.excluded_transitions)
                    ]
                },
            )

    transitions = getattr(Model(), 'get_all_{}_transitions'.format(fieldname))()
    transition_names = set(x.name for x in transitions)

    for transition_name in transition_names:
        url = transition_name.replace('_', '-')
        setattr(
            Mixin,
            transition_name,
            action(methods=['POST'], detail=True, url_name=url, url_path=url)(
                get_transition_viewset_method(transition_name)
            ),
        )

    return Mixin
