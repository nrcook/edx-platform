"""
Provides Python APIs exposed from Grades models.
"""
from lms.djangoapps.grades.models import (
    PersistentCourseGrade as _PersistentCourseGrade,
    PersistentSubsectionGrade as _PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride as _PersistentSubsectionGradeOverride,
    VisibleBlocks as _VisibleBlocks,
)
from lms.djangoapps.utils import _get_key

from opaque_keys.edx.keys import CourseKey, UsageKey


def prefetch_grade_overrides_and_visible_blocks(user, course_key):
    _PersistentSubsectionGradeOverride.prefetch(user.id, course_key)
    _VisibleBlocks.bulk_read(user.id, course_key)


def prefetch_course_grades(course_key, users):
    _PersistentCourseGrade.prefetch(course_key, users)


def prefetch_course_and_subsection_grades(course_key, users):
    _PersistentCourseGrade.prefetch(course_key, users)
    _PersistentSubsectionGrade.prefetch(course_key, users)


def clear_prefetched_course_grades(course_key):
    _PersistentCourseGrade.clear_prefetched_data(course_key)
    _PersistentSubsectionGrade.clear_prefetched_data(course_key)


def clear_prefetched_course_and_subsection_grades(course_key):
    _PersistentCourseGrade.clear_prefetched_data(course_key)


def get_recently_modified_grades(course_keys, start_date, end_date):
    """
    Returns a QuerySet of PersistentCourseGrade objects filtered by the input
    parameters and ordered by modified date.
    """
    grade_filter_args = {}
    if course_keys:
        grade_filter_args['course_id__in'] = course_keys
    if start_date:
        grade_filter_args['modified__gte'] = start_date
    if end_date:
        grade_filter_args['modified__lte'] = end_date

    return _PersistentCourseGrade.objects.filter(**grade_filter_args).order_by('modified')


def get_subsection_grade(user_id, course_key_or_id, usage_key_or_id):
    """
    Finds and returns the earned subsection grade for user
    """
    course_key = _get_key(course_key_or_id, CourseKey)
    usage_key = _get_key(usage_key_or_id, UsageKey)

    return _PersistentSubsectionGrade.objects.get(
        user_id=user_id,
        course_id=course_key,
        usage_key=usage_key
    )


def get_subsection_grades(user_id, course_key_or_id):
    """
    Return dictionary of grades for user_id.
    """
    course_key = _get_key(course_key_or_id, CourseKey)
    grades = {}
    for grade in _PersistentSubsectionGrade.bulk_read_grades(user_id, course_key_or_id):
        grades[grade.usage_key] = grade
    return grades


def get_subsection_grade_override(user_id, course_key_or_id, usage_key_or_id):
    """
    Finds the subsection grade for user and returns the override for that grade if it exists

    If override does not exist, returns None. If subsection grade does not exist, will raise an exception.
    """
    usage_key = _get_key(usage_key_or_id, UsageKey)

    # Verify that a corresponding subsection grade exists for the given user and usage_key
    # Raises PersistentSubsectionGrade.DoesNotExist if it does not exist.
    _ = get_subsection_grade(user_id, course_key_or_id, usage_key_or_id)

    return _PersistentSubsectionGradeOverride.get_override(user_id, usage_key)
