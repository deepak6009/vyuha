"""Functionality for communicating the required user interface for a thing."""

from typing import Any

from pydantic import BaseModel

# import labthings_fastapi as lt


class ActionButton(BaseModel):
    """The data required for creating an actionButton in Vue.

    Currently this cannot be used to submitData, or to submitOnEvent.
    """

    thing: str
    """The Thing "path" for the Thing instance."""

    action: str
    """The name of the action to be triggered."""

    poll_interval: int = 1
    """The interval for polling in seconds."""

    submit_label: str = "Submit"
    """The label for the action button."""

    can_terminate: bool = True
    """Specify whether the action can be terminated."""

    requires_confirmation: bool = False
    """Specify whether a confirmation modal is needed."""

    confirmation_message: str = "Start task?"
    """The message for the confirmation modal."""

    button_primary: bool = True
    """Specify whether the button is styled as a primary button."""

    modal_progress: bool = False
    """Specify whether to show a progress modal."""

    notify_on_success: bool = False
    """Specify whether to a notification on successful completion."""

    success_message: str = "Success!"
    """The message to show on successful completion."""


def action_button_for(thing: Any, action_name: str, **kwargs: Any) -> ActionButton:
    """Create a ActionButton data for the specified Thing Action.

    :param thing:  The instance of the thing that has the action.
    :param action_name: The name of the action to create a button for.
    :param kwargs: Any attribute of `ActionButton` except for ``thing`` or ``action``.
    :return: An ActionButton (Pydantic Model) object with all the information the
        webapp needs to create the action button.
    """
    return ActionButton(thing=thing.name, action=action_name, **kwargs)


class PropertyControl(BaseModel):
    """The data required for creating an actionButton in Vue.

    Currently this cannot be used to submitData, or to submitOnEvent.
    """

    thing: str
    """The Thing "path" for the Thing instance."""

    property_name: str
    """The name of the property (or setting)."""

    label: str
    """The label to show in the UI"""

    read_back: bool = False
    """Whether or not to read back the property after setting.

    This is useful for hardware settings that may be coerced to the closest value.
    """

    read_back_delay: int = 1000
    """The delay in ms before reading back the property."""


def property_control_for(
    thing: Any, property_name: str, **kwargs: Any
) -> PropertyControl:
    """Create an PropertyControl data for the specified Thing Property.

    :param thing: The instance of the thing that has the property to be controlled.
    :param property_name: The name of the property to create a control for.
    :param kwargs: Any attribute of `PropertyControl` except for ``thing`` or
        ``property_name``. If label is not set here it will be the property name.
    """
    if "label" not in kwargs:
        kwargs["label"] = property_name
    return PropertyControl(thing=thing.name, property_name=property_name, **kwargs)
