from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CategoryForm
from .models import Category

staff_required = user_passes_test(lambda u: u.is_staff)


def category_list(request):
    categories = Category.objects.annotate(event_count=Count('events')).order_by('name')
    return render(request, 'categories/category_list.html', {'categories': categories})


@login_required
@staff_required
def category_create(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category created successfully.")
            return redirect('categories:category_list')
        messages.error(request, "Please correct the errors below.")
    else:
        form = CategoryForm()
    return render(request, 'categories/category_form.html', {'form': form, 'title': 'Add Category'})


@login_required
@staff_required
def category_update(request, slug):
    category = get_object_or_404(Category, slug=slug)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated successfully.")
            return redirect('categories:category_list')
        messages.error(request, "Please correct the errors below.")
    else:
        form = CategoryForm(instance=category)
    return render(request, 'categories/category_form.html', {'form': form, 'title': 'Edit Category'})


@login_required
@staff_required
def category_delete(request, slug):
    category = get_object_or_404(Category, slug=slug)
    if request.method == 'POST':
        category.delete()
        messages.success(request, "Category deleted successfully.")
        return redirect('categories:category_list')
    return render(request, 'categories/category_confirm_delete.html', {'category': category})
