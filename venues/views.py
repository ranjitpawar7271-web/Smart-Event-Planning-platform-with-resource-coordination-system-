import calendar
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from users.models import User
from users.permissions import role_required
from .forms import MaintenanceScheduleForm, VenueBookingForm, VenueForm
from .models import MaintenanceSchedule, Venue, VenueBooking

VENUE_MANAGER_ROLES = (User.SUPER_ADMIN, User.STAFF)


def venue_list(request):
    venues = Venue.objects.filter(is_active=True)

    query = request.GET.get('q', '').strip()
    city = request.GET.get('city', '')
    min_capacity = request.GET.get('min_capacity', '')

    if query:
        venues = venues.filter(Q(name__icontains=query) | Q(address__icontains=query))
    if city:
        venues = venues.filter(city__iexact=city)
    if min_capacity.isdigit():
        venues = venues.filter(capacity__gte=int(min_capacity))

    paginator = Paginator(venues.order_by('name'), 9)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'query': query,
        'selected_city': city,
        'min_capacity': min_capacity,
        'cities': Venue.objects.filter(is_active=True).values_list('city', flat=True).distinct().order_by('city'),
        'can_manage_venues': request.user.is_authenticated and (
            request.user.is_superuser or request.user.role in VENUE_MANAGER_ROLES
        ),
    }
    return render(request, 'venues/venue_list.html', context)


def venue_detail(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    upcoming_bookings = venue.bookings.filter(status='confirmed').order_by('start_datetime')[:10]
    upcoming_maintenance = venue.maintenance_windows.order_by('start_datetime')[:10]
    context = {
        'venue': venue,
        'upcoming_bookings': upcoming_bookings,
        'upcoming_maintenance': upcoming_maintenance,
        'can_manage': request.user.is_authenticated and (
            request.user.is_superuser or request.user.role in VENUE_MANAGER_ROLES
        ),
    }
    return render(request, 'venues/venue_detail.html', context)


@role_required(*VENUE_MANAGER_ROLES)
def venue_create(request):
    if request.method == 'POST':
        form = VenueForm(request.POST, request.FILES)
        if form.is_valid():
            venue = form.save(commit=False)
            venue.created_by = request.user
            venue.save()
            messages.success(request, "Venue created successfully.")
            return redirect('venues:venue_detail', slug=venue.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        form = VenueForm()
    return render(request, 'venues/venue_form.html', {'form': form, 'title': 'Add Venue'})


@role_required(*VENUE_MANAGER_ROLES)
def venue_update(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    if request.method == 'POST':
        form = VenueForm(request.POST, request.FILES, instance=venue)
        if form.is_valid():
            form.save()
            messages.success(request, "Venue updated successfully.")
            return redirect('venues:venue_detail', slug=venue.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        form = VenueForm(instance=venue)
    return render(request, 'venues/venue_form.html', {'form': form, 'title': 'Edit Venue', 'venue': venue})


@role_required(*VENUE_MANAGER_ROLES)
def venue_delete(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    if request.method == 'POST':
        venue.delete()
        messages.success(request, "Venue deleted successfully.")
        return redirect('venues:venue_list')
    return render(request, 'venues/venue_confirm_delete.html', {'venue': venue})


@login_required
def venue_calendar(request, slug):
    """Simple month-view availability calendar for a venue."""
    venue = get_object_or_404(Venue, slug=slug)

    today = date.today()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
    except ValueError:
        year, month = today.year, today.month

    cal = calendar.Calendar(firstweekday=0)
    month_days = [d for d in cal.itermonthdates(year, month)]

    bookings = venue.bookings.filter(
        status='confirmed', start_datetime__year=year, start_datetime__month=month
    )
    maintenance = venue.maintenance_windows.filter(start_datetime__year=year, start_datetime__month=month)

    busy_days = {b.start_datetime.date() for b in bookings} | {m.start_datetime.date() for m in maintenance}

    weeks = [month_days[i:i + 7] for i in range(0, len(month_days), 7)]

    prev_month = (month - 1) or 12
    prev_year = year - 1 if month == 1 else year
    next_month = (month % 12) + 1
    next_year = year + 1 if month == 12 else year

    context = {
        'venue': venue,
        'weeks': weeks,
        'current_month': date(year, month, 1),
        'busy_days': busy_days,
        'today': today,
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'bookings': bookings.order_by('start_datetime'),
        'maintenance': maintenance.order_by('start_datetime'),
        'can_manage': request.user.is_superuser or request.user.role in VENUE_MANAGER_ROLES,
    }
    return render(request, 'venues/venue_calendar.html', context)


@login_required
def venue_booking_create(request, slug=None):
    initial = {}
    venue = None
    if slug:
        venue = get_object_or_404(Venue, slug=slug)
        initial['venue'] = venue

    if request.method == 'POST':
        form = VenueBookingForm(request.POST, initial=initial)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.booked_by = request.user
            booking.save()
            messages.success(request, "Venue booked successfully.")
            return redirect('venues:venue_detail', slug=booking.venue.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        form = VenueBookingForm(initial=initial)
    return render(request, 'venues/venue_booking_form.html', {'form': form, 'venue': venue})


@login_required
def venue_booking_cancel(request, pk):
    booking = get_object_or_404(VenueBooking, pk=pk)
    if booking.booked_by != request.user and not (
        request.user.is_superuser or request.user.role in VENUE_MANAGER_ROLES
    ):
        messages.error(request, "You are not authorized to cancel this booking.")
        return redirect('venues:venue_detail', slug=booking.venue.slug)

    if request.method == 'POST':
        booking.status = 'cancelled'
        booking.save()
        messages.success(request, "Booking cancelled.")
        return redirect('venues:venue_detail', slug=booking.venue.slug)
    return render(request, 'venues/venue_booking_confirm_cancel.html', {'booking': booking})


@role_required(*VENUE_MANAGER_ROLES)
def maintenance_create(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    if request.method == 'POST':
        form = MaintenanceScheduleForm(request.POST, initial={'venue': venue})
        if form.is_valid():
            maintenance = form.save(commit=False)
            maintenance.created_by = request.user
            maintenance.save()
            messages.success(request, "Maintenance window scheduled.")
            return redirect('venues:venue_calendar', slug=venue.slug)
        messages.error(request, "Please correct the errors below.")
    else:
        form = MaintenanceScheduleForm(initial={'venue': venue})
    return render(request, 'venues/maintenance_form.html', {'form': form, 'venue': venue})


@role_required(*VENUE_MANAGER_ROLES)
def maintenance_delete(request, pk):
    maintenance = get_object_or_404(MaintenanceSchedule, pk=pk)
    slug = maintenance.venue.slug
    if request.method == 'POST':
        maintenance.delete()
        messages.success(request, "Maintenance window removed.")
    return redirect('venues:venue_calendar', slug=slug)
