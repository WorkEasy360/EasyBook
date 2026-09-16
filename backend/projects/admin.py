from django.contrib import admin

from projects.models.project import Project, ProjectMember, Task
from projects.models.time_entry import TimeEntry


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["project_code", "name", "customer", "organization", "status", "billing_method"]
    list_filter = ["status", "billing_method"]
    search_fields = ["project_code", "name"]


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ["project", "user", "billable_rate", "cost_rate", "is_active"]
    list_filter = ["is_active"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "organization", "is_billable", "is_active", "hourly_rate"]
    list_filter = ["is_billable", "is_active"]
    search_fields = ["name"]


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ["entry_date", "user", "project", "task", "hours", "status", "is_billable"]
    list_filter = ["status", "is_billable"]
    date_hierarchy = "entry_date"
