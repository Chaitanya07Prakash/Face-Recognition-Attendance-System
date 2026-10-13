from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from accounts.models import CustomUser
from accounts.rbac import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Creates a Django Group per CustomUser.Role and assigns permissions from accounts.rbac"

    def handle(self, *args, **options):
        for role, mapping in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=role)
            group.permissions.clear()

            if mapping.get("_is_superuser_role"):
                self.stdout.write(
                    f"{'Created' if created else 'Updated'} group '{role}' "
                    "(Admins should also have is_superuser=True on their user record)"
                )
                continue

            granted = 0
            for model_label, actions in mapping.items():
                app_label, model_name = model_label.split(".")
                try:
                    model = apps.get_model(app_label, model_name)
                except LookupError:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  skipping {model_label}: app not installed yet (add it in a later phase)"
                        )
                    )
                    continue

                content_type_model_name = model._meta.model_name
                for action in set(actions) | {"view"}:  # view is always implied
                    codename = f"{action}_{content_type_model_name}"
                    try:
                        perm = Permission.objects.get(
                            codename=codename,
                            content_type__app_label=app_label,
                        )
                    except Permission.DoesNotExist:
                        continue
                    group.permissions.add(perm)
                    granted += 1

            self.stdout.write(f"{'Created' if created else 'Updated'} group '{role}': {granted} permissions")

        self.stdout.write(self.style.SUCCESS("Role seeding complete."))
