"""
Cross-module glue, wired onto other apps' models via signals rather than
editing their views/forms directly — same additive pattern Module 7 uses
for auto-issuing tickets off `events.Registration`.

Two things happen here:

1. The Event Draft -> Published approval gate (pre_save + post_save on
   Event). When WorkflowSettings.require_event_approval is on, a save
   that tries to publish an event without a prior approved ApprovalStep
   is quietly held back to Draft, and a pending ApprovalStep + a
   notification to the organizer and to approvers are created instead.

2. Immediate "something happened, tell someone" notifications for staff
   assignment (Module 5) and vendor contracts being sent (Module 4). The
   *scheduled* reminders for these (a shift starting tomorrow, a contract
   still unsigned after two days) live in management/commands/send_reminders.py
   instead, since those need a clock, not an event.
"""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

from events.models import Event
from staff.models import ShiftAssignment
from users.models import User
from vendors.models import VendorContract

from .models import ApprovalStep, Notification, WorkflowSettings


def _admin_users():
    return User.objects.filter(
        Q(role=User.SUPER_ADMIN) | Q(role=User.STAFF) | Q(is_superuser=True)
    ).distinct()


# --- 1. Event publish gate -------------------------------------------

@receiver(pre_save, sender=Event)
def guard_event_publish(sender, instance, **kwargs):
    instance._workflow_needs_approval = False

    if instance.status != 'published':
        return

    if not WorkflowSettings.get_solo().require_event_approval:
        return

    already_approved = False
    if instance.pk:
        already_approved = ApprovalStep.objects.filter(
            content_type=ContentType.objects.get_for_model(Event),
            object_id=instance.pk,
            stage=ApprovalStep.STAGE_PUBLISHED,
            status=ApprovalStep.STATUS_APPROVED,
        ).exists()

    if already_approved:
        return

    # Hold it back — the organizer gets a notification explaining why in
    # the post_save handler below, once we're sure this actually saved.
    instance.status = 'draft'
    instance._workflow_needs_approval = True


@receiver(post_save, sender=Event)
def request_event_approval(sender, instance, created, **kwargs):
    if not getattr(instance, '_workflow_needs_approval', False):
        return

    step, was_created = ApprovalStep.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(Event),
        object_id=instance.pk,
        stage=ApprovalStep.STAGE_PUBLISHED,
        status=ApprovalStep.STATUS_PENDING,
        defaults={'requested_by': instance.organizer},
    )
    if not was_created:
        return

    approvals_url = reverse('workflow:approval_list')

    if instance.organizer:
        Notification.notify(
            instance.organizer,
            f"'{instance.title}' is held as Draft pending approval to publish.",
            link=instance.get_absolute_url(),
            notification_type=Notification.TYPE_APPROVAL,
            dedupe_key=f'approval-submitted-{step.pk}',
        )

    for admin in _admin_users():
        Notification.notify(
            admin,
            f"Approval needed: publish '{instance.title}'.",
            link=approvals_url,
            notification_type=Notification.TYPE_APPROVAL,
            dedupe_key=f'approval-requested-{step.pk}-{admin.pk}',
        )


# --- 2. Staff assignment notification (Module 5) ----------------------

@receiver(post_save, sender=ShiftAssignment)
def notify_staff_assignment(sender, instance, created, **kwargs):
    if not created:
        return
    staff_user = instance.staff.user
    when = instance.start_datetime.strftime('%b %d, %Y %H:%M')
    Notification.notify(
        staff_user,
        f"You've been assigned to '{instance.title}' starting {when}.",
        link=reverse('staff:staff_detail', kwargs={'pk': instance.staff.pk}),
        notification_type=Notification.TYPE_STAFF,
        dedupe_key=f'shift-assigned-{instance.pk}',
    )


# --- 3. Vendor contract sent for signature (Module 4) ------------------

@receiver(post_save, sender=VendorContract)
def notify_vendor_contract_sent(sender, instance, created, **kwargs):
    if instance.status != 'sent':
        return
    vendor_user = instance.vendor.user
    Notification.notify(
        vendor_user,
        f"Contract '{instance.title}' is ready for your signature.",
        link=instance.vendor.get_absolute_url(),
        notification_type=Notification.TYPE_VENDOR,
        dedupe_key=f'contract-sent-{instance.pk}',
    )
