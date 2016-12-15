"""
xModule implementation of a learning sequence
"""

# pylint: disable=abstract-method

import json
import logging
from pkg_resources import resource_string
import warnings

from lxml import etree
from xblock.core import XBlock
from xblock.fields import Integer, Scope, Boolean
from xblock.fragment import Fragment

from .exceptions import NotFoundError
from .fields import Date
from .mako_module import MakoModuleDescriptor
from .progress import Progress
from .x_module import XModule, STUDENT_VIEW
from .xml_module import XmlDescriptor

log = logging.getLogger(__name__)

# HACK: This shouldn't be hard-coded to two types
# OBSOLETE: This obsoletes 'type'
class_priority = ['video', 'problem']

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


class SequenceFields(object):
    has_children = True

    # NOTE: Position is 1-indexed.  This is silly, but there are now student
    # positions saved on prod, so it's not easy to fix.
    position = Integer(help="Last tab viewed in this sequence", scope=Scope.user_state)
    due = Date(
        display_name=_("Due Date"),
        help=_("Enter the date by which problems are due."),
        scope=Scope.settings,
    )

    # Entrance Exam flag -- see cms/contentstore/views/entrance_exam.py for usage
    is_entrance_exam = Boolean(
        display_name=_("Is Entrance Exam"),
        help=_(
            "Tag this course module as an Entrance Exam. "
            "Note, you must enable Entrance Exams for this course setting to take effect."
        ),
        default=False,
        scope=Scope.settings,
    )


class ProctoringFields(object):
    """
    Fields that are specific to Proctored or Timed Exams
    """
    is_time_limited = Boolean(
        display_name=_("Is Time Limited"),
        help=_(
            "This setting indicates whether students have a limited time"
            " to view or interact with this courseware component."
        ),
        default=False,
        scope=Scope.settings,
    )

    default_time_limit_minutes = Integer(
        display_name=_("Time Limit in Minutes"),
        help=_(
            "The number of minutes available to students for viewing or interacting with this courseware component."
        ),
        default=None,
        scope=Scope.settings,
    )

    is_proctored_enabled = Boolean(
        display_name=_("Is Proctoring Enabled"),
        help=_(
            "This setting indicates whether this exam is a proctored exam."
        ),
        default=False,
        scope=Scope.settings,
    )

    is_practice_exam = Boolean(
        display_name=_("Is Practice Exam"),
        help=_(
            "This setting indicates whether this exam is for testing purposes only. Practice exams are not verified."
        ),
        default=False,
        scope=Scope.settings,
    )

    @property
    def is_proctored_exam(self):
        """ Alias the is_proctored_enabled field to the more legible is_proctored_exam """
        return self.is_proctored_enabled

    @is_proctored_exam.setter
    def is_proctored_exam(self, value):
        """ Alias the is_proctored_enabled field to the more legible is_proctored_exam """
        self.is_proctored_enabled = value


@XBlock.wants('proctoring')
@XBlock.wants('credit')
@XBlock.needs('bookmarks')
class SequenceModule(SequenceFields, ProctoringFields, XModule):
    ''' Layout module which lays out content in a temporal sequence
    '''
    js = {
        'coffee': [resource_string(__name__, 'js/src/sequence/display.coffee')],
        'js': [resource_string(__name__, 'js/src/sequence/display/jquery.sequence.js')],
    }
    css = {
        'scss': [resource_string(__name__, 'css/sequence/display.scss')],
    }
    js_module_name = "Sequence"

    def __init__(self, *args, **kwargs):
        super(SequenceModule, self).__init__(*args, **kwargs)

        # If position is specified in system, then use that instead.
        position = getattr(self.system, 'position', None)
        if position is not None:
            assert isinstance(position, int)
            self.position = self.system.position

    def get_progress(self):
        ''' Return the total progress, adding total done and total available.
        (assumes that each submodule uses the same "units" for progress.)
        '''
        # TODO: Cache progress or children array?
        children = self.get_children()
        progresses = [child.get_progress() for child in children]
        progress = reduce(Progress.add_counts, progresses, None)
        return progress

    def handle_ajax(self, dispatch, data):  # TODO: bounds checking
        ''' get = request.POST instance '''
        if dispatch == 'goto_position':
            # set position to default value if either 'position' argument not
            # found in request or it is a non-positive integer
            position = data.get('position', u'1')
            if position.isdigit() and int(position) > 0:
                self.position = int(position)
            else:
                self.position = 1
            return json.dumps({'success': True})

        raise NotFoundError('Unexpected dispatch type')

    def student_view(self, context):
        display_items = self.get_display_items()

        # If we're rendering this sequence, but no position is set yet,
        # or exceeds the length of the displayable items,
        # default the position to the first element
        if context.get('requested_child') == 'first':
            self.position = 1
        elif context.get('requested_child') == 'last':
            self.position = len(display_items) or None
        elif self.position is None or self.position > len(display_items):
            self.position = 1

        ## Returns a set of all types of all sub-children
        contents = []

        fragment = Fragment()
        context = context or {}

        bookmarks_service = self.runtime.service(self, "bookmarks")
        context["username"] = self.runtime.service(self, "user").get_current_user().opt_attrs['edx-platform.username']

        parent_module = self.get_parent()
        display_names = [
            parent_module.display_name_with_default,
            self.display_name_with_default
        ]

        # We do this up here because proctored exam functionality could bypass
        # rendering after this section.
        self._capture_basic_metrics()

        # Is this sequential part of a timed or proctored exam?
        if self.is_time_limited:
            view_html = self._time_limited_student_view(context)

            # Do we have an alternate rendering
            # from the edx_proctoring subsystem?
            if view_html:
                fragment.add_content(view_html)
                return fragment

        for child in display_items:
            is_bookmarked = bookmarks_service.is_bookmarked(usage_key=child.scope_ids.usage_id)
            context["bookmarked"] = is_bookmarked

            progress = child.get_progress()
            rendered_child = child.render(STUDENT_VIEW, context)
            fragment.add_frag_resources(rendered_child)

            # `titles` is a list of titles to inject into the sequential tooltip display.
            # We omit any blank titles to avoid blank lines in the tooltip display.
            titles = [title.strip() for title in child.get_content_titles() if title.strip()]
            childinfo = {
                'content': rendered_child.content,
                'title': "\n".join(titles),
                'page_title': titles[0] if titles else '',
                'progress_status': Progress.to_js_status_str(progress),
                'progress_detail': Progress.to_js_detail_str(progress),
                'type': child.get_icon_class(),
                'id': child.scope_ids.usage_id.to_deprecated_string(),
            }
            if childinfo['title'] == '':
                childinfo['title'] = child.display_name_with_default
            contents.append(childinfo)

        params = {
            'items': contents,
            'element_id': self.location.html_id(),
            'item_id': self.location.to_deprecated_string(),
            'position': self.position,
            'tag': self.location.category,
            'ajax_url': self.system.ajax_url,
            'next_url': _compute_next_url(
                self.location,
                parent_module,
                context.get('redirect_url_func'),
            ),
            'prev_url': _compute_previous_url(
                self.location,
                parent_module,
                context.get('redirect_url_func'),
            ),
        }

        fragment.add_content(self.system.render_template("seq_module.html", params))

        return fragment

    def _time_limited_student_view(self, context):
        """
        Delegated rendering of a student view when in a time
        limited view. This ultimately calls down into edx_proctoring
        pip installed djangoapp
        """

        # None = no overridden view rendering
        view_html = None

        proctoring_service = self.runtime.service(self, 'proctoring')
        credit_service = self.runtime.service(self, 'credit')

        # Is this sequence designated as a Timed Examination, which includes
        # Proctored Exams
        feature_enabled = (
            proctoring_service and
            credit_service and
            self.is_time_limited
        )
        if feature_enabled:
            user_id = self.runtime.user_id
            user_role_in_course = 'staff' if self.runtime.user_is_staff else 'student'
            course_id = self.runtime.course_id
            content_id = self.location

            context = {
                'display_name': self.display_name,
                'default_time_limit_mins': (
                    self.default_time_limit_minutes if
                    self.default_time_limit_minutes else 0
                ),
                'is_practice_exam': self.is_practice_exam,
                'due_date': self.due
            }

            # inject the user's credit requirements and fulfillments
            if credit_service:
                credit_state = credit_service.get_credit_state(user_id, course_id)
                if credit_state:
                    context.update({
                        'credit_state': credit_state
                    })

            # See if the edx-proctoring subsystem wants to present
            # a special view to the student rather
            # than the actual sequence content
            #
            # This will return None if there is no
            # overridden view to display given the
            # current state of the user
            view_html = proctoring_service.get_student_view(
                user_id=user_id,
                course_id=course_id,
                content_id=content_id,
                context=context,
                user_role=user_role_in_course
            )

        return view_html

    def get_icon_class(self):
        child_classes = set(child.get_icon_class()
                            for child in self.get_children())
        new_class = 'other'
        for c in class_priority:
            if c in child_classes:
                new_class = c
        return new_class


class SequenceDescriptor(SequenceFields, ProctoringFields, MakoModuleDescriptor, XmlDescriptor):
    """
    A Sequences Descriptor object
    """
    mako_template = 'widgets/sequence-edit.html'
    module_class = SequenceModule

    show_in_read_only_mode = True

    js = {
        'coffee': [resource_string(__name__, 'js/src/sequence/edit.coffee')],
    }
    js_module_name = "SequenceDescriptor"

    @classmethod
    def definition_from_xml(cls, xml_object, system):
        children = []
        for child in xml_object:
            try:
                child_block = system.process_xml(etree.tostring(child, encoding='unicode'))
                children.append(child_block.scope_ids.usage_id)
            except Exception as e:
                log.exception("Unable to load child when parsing Sequence. Continuing...")
                if system.error_tracker is not None:
                    system.error_tracker(u"ERROR: {0}".format(e))
                continue
        return {}, children

    def definition_to_xml(self, resource_fs):
        xml_object = etree.Element('sequential')
        for child in self.get_children():
            self.runtime.add_block_as_child_node(child, xml_object)
        return xml_object

    @property
    def non_editable_metadata_fields(self):
        """
        `is_entrance_exam` should not be editable in the Studio settings editor.
        """
        non_editable_fields = super(SequenceDescriptor, self).non_editable_metadata_fields
        non_editable_fields.append(self.fields['is_entrance_exam'])
        return non_editable_fields

    def index_dictionary(self):
        """
        Return dictionary prepared with module content and type for indexing.
        """
        # return key/value fields in a Python dict object
        # values may be numeric / string or dict
        # default implementation is an empty dict
        xblock_body = super(SequenceDescriptor, self).index_dictionary()
        html_body = {
            "display_name": self.display_name,
        }
        if "content" in xblock_body:
            xblock_body["content"].update(html_body)
        else:
            xblock_body["content"] = html_body
        xblock_body["content_type"] = "Sequence"

        return xblock_body


def _compute_next_url(block_location, parent_block, redirect_url_func):
    """
    Returns the url for the next block after the given block.
    """
    def get_next_block_location(parent_block, index_in_parent):
        """
        Returns the next block in the parent_block after the block with the given
        index_in_parent.
        """
        if index_in_parent + 1 < len(parent_block.children):
            return parent_block.children[index_in_parent + 1]
        else:
            return None

    return _compute_next_or_prev_url(
        block_location,
        parent_block,
        redirect_url_func,
        get_next_block_location,
        'first',
    )


def _compute_previous_url(block_location, parent_block, redirect_url_func):
    """
    Returns the url for the previous block after the given block.
    """
    def get_previous_block_location(parent_block, index_in_parent):
        """
        Returns the previous block in the parent_block before the block with the given
        index_in_parent.
        """
        return parent_block.children[index_in_parent - 1] if index_in_parent else None

    return _compute_next_or_prev_url(
        block_location,
        parent_block,
        redirect_url_func,
        get_previous_block_location,
        'last',
    )


def _compute_next_or_prev_url(
        block_location,
        parent_block,
        redirect_url_func,
        get_next_or_prev_block,
        redirect_url_child_param,
):
    """
    Returns the url for the next or previous block from the given block.

    Arguments:
        block_location: Location of the block that is being navigated.
        parent_block: Parent block of the given block.
        redirect_url_func: Function that computes a redirect URL directly to
            a block, given the block's location.
        get_next_or_prev_block: Function that returns the next or previous
            block in the parent, or None if doesn't exist.
        redirect_url_child_param: Value to pass for the child parameter to the
            redirect_url_func.
    """
    if redirect_url_func:
        index_in_parent = parent_block.children.index(block_location)
        next_or_prev_block_location = get_next_or_prev_block(parent_block, index_in_parent)
        if next_or_prev_block_location:
            return redirect_url_func(
                block_location.course_key,
                next_or_prev_block_location,
                child=redirect_url_child_param,
            )
        else:
            grandparent = parent_block.get_parent()
            if grandparent:
                return _compute_next_or_prev_url(
                    parent_block.location,
                    grandparent,
                    redirect_url_func,
                    get_next_or_prev_block,
                    redirect_url_child_param,
                )
    return None
