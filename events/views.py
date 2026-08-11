from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from categories.models import Category
from venues.models import VenueBooking
from wishlist.models import FavoriteEvent
from .forms import EventForm
from .event_templates import EVENT_TEMPLATES, get_template_initial
from .ics_utils import build_google_calendar_url, build_ics_bytes
from .models import Event, Registration


def event_list(request):
    events = Event.objects.filter(status='published').select_related('category', 'organizer')

    query = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '')
    sort = request.GET.get('sort', '')

    if query:
        events = events.filter(
            Q(title__icontains=query) | Q(location__icontains=query) | Q(description__icontains=query)
        )
    if category_slug:
        events = events.filter(category__slug=category_slug)

    sort_options = {
        'price_low': 'price',
        'price_high': '-price',
        'newest': '-created_at',
    }
    events = events.order_by(sort_options.get(sort, 'start_date'))

    paginator = Paginator(events, 9)
    page_obj = paginator.get_page(request.GET.get('page'))

    favorited_event_ids = set()
    if request.user.is_authenticated:
        favorited_event_ids = set(
            FavoriteEvent.objects.filter(
                user=request.user, event__in=page_obj.object_list
            ).values_list('event_id', flat=True)
        )

    context = {
        'page_obj': page_obj,
        'categories': Category.objects.all(),
        'query': query,
        'selected_category': category_slug,
        'selected_sort': sort,
        'favorited_event_ids': favorited_event_ids,
    }
    return render(request, 'events/event_list.html', context)


def event_detail(request, slug):
    event = get_object_or_404(
        Event.objects.select_related('category', 'organizer'), slug=slug
    )
    is_registered = False
    if request.user.is_authenticated:
        is_registered = Registration.objects.filter(
            event=event, user=request.user, status='confirmed'
        ).exists()

    # Read-only budget summary for whoever can manage this event's
    # finances (Module 6). Kept as a plain query rather than importing
    # budget's permission helper here, to avoid a circular import between
    # events and budget — the check itself is simple enough to inline.
    budget = None
    can_view_budget = request.user.is_authenticated and request.user.can_manage_events and (
        request.user.is_super_admin or request.user.is_staff_role or event.organizer_id == request.user.id
    )
    if can_view_budget:
        budget = getattr(event, 'budget', None)

    # Sponsorships (Module 10) — same visibility rule as the budget panel,
    # since a sponsorship deal is financial/budget-adjacent data.
    sponsorships = event.sponsorships.select_related('sponsor').all() if can_view_budget else []

    is_favorited = False
    if request.user.is_authenticated:
        is_favorited = FavoriteEvent.objects.filter(user=request.user, event=event).exists()

    context = {
        'event': event,
        'is_registered': is_registered,
        'can_view_budget': can_view_budget,
        'budget': budget,
        'sponsorships': sponsorships,
        'is_favorited': is_favorited,
        'google_calendar_url': build_google_calendar_url(event),
    }
    return render(request, 'events/event_detail.html', context)


def _sync_venue_booking(event, user):
    """Keep a VenueBooking in lockstep with an Event's venue/date selection.

    - No venue selected: cancel any existing linked booking (venue freed up).
    - Venue selected: create the booking, or update the existing one's dates.
    Availability was already validated in EventForm.clean(), so this should
    not normally raise — but VenueBooking.save() re-validates via
    full_clean() as a defense-in-depth safety net.
    """
    existing = event.venue_bookings.filter(status='confirmed').first()

    if not event.venue:
        if existing:
            existing.status = 'cancelled'
            existing.save()
        return

    if existing:
        existing.venue = event.venue
        existing.start_datetime = event.start_date
        existing.end_datetime = event.end_date
        existing.purpose = f"Event: {event.title}"
        existing.save()
    else:
        VenueBooking.objects.create(
            venue=event.venue,
            event=event,
            booked_by=user,
            purpose=f"Event: {event.title}",
            start_datetime=event.start_date,
            end_datetime=event.end_date,
            status='confirmed',
        )


@login_required
def event_create(request):
    if not request.user.can_manage_events:
        messages.error(
            request,
            "Your account role doesn't allow creating events. "
            "Contact an admin if you need Organizer access."
        )
        return redirect('dashboard:dashboard')

    if request.method == 'POST':
        form = EventForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            event = form.save(commit=False)
            event.organizer = request.user
            event.save()
            _sync_venue_booking(event, request.user)
            if not request.user.is_organizer:
                request.user.is_organizer = True
                request.user.save(update_fields=['is_organizer'])
            messages.success(request, "Event created successfully.")
            return redirect('events:event_detail', slug=event.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        initial = get_template_initial(request.GET.get('template', ''))
        form = EventForm(initial=initial, user=request.user)
    return render(request, 'events/event_form.html', {'form': form, 'title': 'Create Event'})


@login_required
def event_create_start(request):
    """Event Templates (Module 10): a picker page linking into the
    normal event_create form with `?template=<key>` pre-filling it, or a
    'start from scratch' link straight to the blank form. Purely a
    convenience layer in front of the existing create flow — no new URL
    behavior for event_create itself, so nothing that already links or
    posts there needed to change."""
    if not request.user.can_manage_events:
        messages.error(
            request,
            "Your account role doesn't allow creating events. "
            "Contact an admin if you need Organizer access."
        )
        return redirect('dashboard:dashboard')
    return render(request, 'events/event_template_picker.html', {'templates': EVENT_TEMPLATES})


@login_required
def event_update(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.organizer != request.user and not request.user.is_staff and not request.user.is_super_admin and not request.user.is_staff_role:
        messages.error(request, "You are not authorized to edit this event.")
        return redirect('events:event_detail', slug=slug)

    if request.method == 'POST':
        form = EventForm(request.POST, request.FILES, instance=event, user=request.user)
        if form.is_valid():
            form.save()
            _sync_venue_booking(event, request.user)
            messages.success(request, "Event updated successfully.")
            return redirect('events:event_detail', slug=event.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        form = EventForm(instance=event, user=request.user)
    return render(request, 'events/event_form.html', {'form': form, 'title': 'Update Event', 'event': event})


@login_required
def event_delete(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.organizer != request.user and not request.user.is_staff and not request.user.is_super_admin and not request.user.is_staff_role:
        messages.error(request, "You are not authorized to delete this event.")
        return redirect('events:event_detail', slug=slug)

    if request.method == 'POST':
        event.delete()
        messages.success(request, "Event deleted successfully.")
        return redirect('events:my_events')
    return render(request, 'events/event_confirm_delete.html', {'event': event})


@login_required
def event_participants(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.organizer != request.user and not request.user.is_staff and not request.user.is_super_admin and not request.user.is_staff_role:
        messages.error(request, "You are not authorized to view participants for this event.")
        return redirect('events:event_detail', slug=slug)

    participants = (
        Registration.objects.filter(event=event, status='confirmed')
        .select_related('user')
        .order_by('-registered_at')
    )

    query = request.GET.get('q', '').strip()
    if query:
        participants = participants.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__username__icontains=query)
            | Q(user__email__icontains=query)
        )

    context = {
        'event': event,
        'participants': participants,
        'query': query,
    }
    return render(request, 'events/event_participants.html', context)


@login_required
def my_events(request):
    events = Event.objects.filter(organizer=request.user).select_related('category')
    return render(request, 'events/my_events.html', {'events': events})


@login_required
def event_register(request, slug):
    event = get_object_or_404(Event, slug=slug)

    if event.is_full:
        messages.warning(request, "Sorry, this event is already full.")
        return redirect('events:event_detail', slug=slug)

    registration, created = Registration.objects.get_or_create(
        event=event, user=request.user, defaults={'status': 'confirmed'}
    )
    if not created:
        registration.status = 'confirmed'
        registration.save(update_fields=['status'])

    messages.success(request, f"You're registered for {event.title}!")
    return redirect('events:event_detail', slug=slug)


@login_required
def event_cancel_registration(request, slug):
    event = get_object_or_404(Event, slug=slug)
    registration = get_object_or_404(Registration, event=event, user=request.user)

    if request.method == 'POST':
        registration.status = 'cancelled'
        registration.save(update_fields=['status'])
        messages.success(request, f"Your registration for {event.title} has been cancelled.")
        return redirect('events:my_registrations')
    return render(request, 'events/registration_confirm_cancel.html', {'registration': registration})


@login_required
def my_registrations(request):
    registrations = (
        Registration.objects.filter(user=request.user)
        .select_related('event', 'event__category')
        .order_by('-registered_at')
    )
    return render(request, 'events/my_registrations.html', {'registrations': registrations})


def event_ics(request, slug):
    """Export to Google Calendar (Module 10): a downloadable .ics file.
    Public, same as event_detail — anyone who can see the event can add
    it to their own calendar without needing an account."""
    event = get_object_or_404(Event, slug=slug)
    ics_bytes = build_ics_bytes(event)
    response = HttpResponse(ics_bytes, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{event.slug}.ics"'
    return response
