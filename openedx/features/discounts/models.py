"""
DiscountRestrictionConfig Models
"""

# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

from openedx.core.djangoapps.config_model_utils.models import StackedConfigurationModel


@python_2_unicode_compatible
class DiscountRestrictionConfig(StackedConfigurationModel):
    """
    A ConfigurationModel used to manage restrictons for lms-controlled discounts
    """

    STACKABLE_FIELDS = ('disabled',)
    disabled = models.NullBooleanField(default=None, verbose_name=_("Disabled"))

    @classmethod
    def disabled_for_course(cls, course):
        """
        Return whether lms-controlled discounts are disabled for this course.
        Checks if discounts are disabled for attributes of this course like Site, Partner, Course or Course Run.

        Arguments:
            course: The CourseOverview of the course being queried.
        """
        current_config = cls.current(course_key=course.id)
        return current_config.disabled

    def __str__(self):
        return "DiscountRestrictionConfig(disabled={!r})".format(
            self.disabled
        )
