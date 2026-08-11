"""
Scheduled reminders: registration closing soon, event tomorrow, payment
pending, staff assignment, vendor reminder — the exact list from the
Module 9 spec.

This project doesn't run a background scheduler (no Celery/cron worker
in this environment), so nothing calls this automatically. In
production, wire it up as either:

    # crontab, every 30 minutes
    */30 * * * * cd /path/to/project && python manage.py send_reminders

    # or a Celery beat task calling the same command:
    from django.core.management import call_command
    call_command('send_reminders')

Every notification created here sets a `dedupe_key`, so running this
command repeatedly (every 30 minutes, forever) never sends the same
reminder twice — Notification.notify() no-ops on a key it's already seen.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from budget.models import Expense
from events.models import Event, Registration
from staff.models import ShiftAssignment
from vendors.models import VendorContract, VendorPayment
from workflow.models import Notification


class Command(BaseCommand):
    help = "Sends the scheduled Module 9 reminders (registration closing, event tomorrow, payment pending, staff assignment, vendor reminder)."

    def handle(self, *args, **options):
        now = timezone.now()
        counts = {
            'registration_closing': self._registration_closing_soon(now),
            'event_tomorrow': self._event_tomorrow(now),
            'payment_pending': self._payment_pending(now),
            'staff_shift_reminder': self._staff_shift_reminder(now),
            'vendor_reminder': self._vendor_reminder(now),
        }
        total = sum(counts.values())
        for rule, count in counts.items():
            self.stdout.write(f"  {rule}: {count} notification(s)")
        self.stdout.write(self.style.SUCCESS(f"send_reminders done — {total} notification(s) sent."))

    # --- Registration closing soon (event starts within 24h, seats open) ---
    def _registration_closing_soon(self, now):
        sent = 0
        window_end = now + timedelta(hours=24)
        events = Event.objects.filter(
            status='published', start_date__gte=now, start_date__lte=window_end,
        )
        for event in events:
            if event.is_full or not event.organizer:
                continue
            notification = Notification.notify(
                event.organizer,
                f"Registration for '{event.title}' closes soon — {event.seats_left} seat(s) left.",
                link=event.get_absolute_url(),
                notification_type=Notification.TYPE_REMINDER,
                dedupe_key=f'reg-closing-{event.pk}',
            )
            sent += 1 if notification else 0
        return sent

    # --- Event tomorrow (23-25h out), notify registrants + organizer ------
    def _event_tomorrow(self, now):
        sent = 0
        window_start = now + timedelta(hours=23)
        window_end = now + timedelta(hours=25)
        events = Event.objects.filter(
            status='published', start_date__gte=window_start, start_date__lte=window_end,
        )
        for event in events:
            when = event.start_date.strftime('%H:%M')
            if event.organizer:
                notification = Notification.notify(
                    event.organizer,
                    f"'{event.title}' is happening tomorrow at {when}.",
                    link=event.get_absolute_url(),
                    notification_type=Notification.TYPE_REMINDER,
                    dedupe_key=f'event-tomorrow-organizer-{event.pk}',
                )
                sent += 1 if notification else 0

            registrants = Registration.objects.filter(event=event, status='confirmed').select_related('user')
            for registration in registrants:
                notification = Notification.notify(
                    registration.user,
                    f"Reminder: '{event.title}' is tomorrow at {when}.",
                    link=event.get_absolute_url(),
                    notification_type=Notification.TYPE_REMINDER,
                    dedupe_key=f'event-tomorrow-{event.pk}-{registration.user_id}',
                )
                sent += 1 if notification else 0
        return sent

    # --- Payment pending (Module 6 expenses + vendor payments) -----------
    def _payment_pending(self, now):
        sent = 0
        stale_cutoff = now - timedelta(days=2)

        expenses = Expense.objects.filter(
            status='pending', created_at__lte=stale_cutoff
        ).select_related('budget__event__organizer')
        for expense in expenses:
            organizer = expense.budget.event.organizer
            if not organizer:
                continue
            notification = Notification.notify(
                organizer,
                f"Expense '{expense.description}' (₹{expense.amount}) on '{expense.budget.event.title}' is still pending approval.",
                link=expense.budget.get_absolute_url(),
                notification_type=Notification.TYPE_PAYMENT,
                dedupe_key=f'expense-pending-{expense.pk}',
            )
            sent += 1 if notification else 0

        vendor_payments = VendorPayment.objects.filter(
            status='pending', payment_date__lte=(now + timedelta(days=2)).date()
        ).select_related('vendor__user')
        for payment in vendor_payments:
            notification = Notification.notify(
                payment.vendor.user,
                f"A pending payment of ₹{payment.amount} to you is due {payment.payment_date}.",
                link=payment.vendor.get_absolute_url(),
                notification_type=Notification.TYPE_PAYMENT,
                dedupe_key=f'vendor-payment-pending-{payment.pk}',
            )
            sent += 1 if notification else 0
        return sent

    # --- Staff assignment reminder (shift starting within 24h) -----------
    def _staff_shift_reminder(self, now):
        sent = 0
        window_end = now + timedelta(hours=24)
        shifts = ShiftAssignment.objects.filter(
            status='assigned', start_datetime__gte=now, start_datetime__lte=window_end,
        ).select_related('staff__user')
        for shift in shifts:
            when = shift.start_datetime.strftime('%b %d, %H:%M')
            notification = Notification.notify(
                shift.staff.user,
                f"Upcoming shift: '{shift.title}' starts {when}.",
                link=reverse('staff:staff_detail', kwargs={'pk': shift.staff.pk}),
                notification_type=Notification.TYPE_STAFF,
                dedupe_key=f'shift-reminder-{shift.pk}',
            )
            sent += 1 if notification else 0
        return sent

    # --- Vendor reminder (contract sent but unsigned after 2 days) -------
    def _vendor_reminder(self, now):
        sent = 0
        stale_cutoff = now - timedelta(days=2)
        contracts = VendorContract.objects.filter(
            status='sent', created_at__lte=stale_cutoff
        ).select_related('vendor__user')
        for contract in contracts:
            notification = Notification.notify(
                contract.vendor.user,
                f"Contract '{contract.title}' is still awaiting your signature.",
                link=contract.vendor.get_absolute_url(),
                notification_type=Notification.TYPE_VENDOR,
                dedupe_key=f'contract-reminder-{contract.pk}',
            )
            sent += 1 if notification else 0
        return sent
