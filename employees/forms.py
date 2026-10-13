from django import forms

from employees.models import Employee
from locations.models import Area, Department, Mine, Project


class EmployeeForm(forms.ModelForm):
    """
    `area`, `project`, `mine` are not Employee model fields — they exist
    purely to drive the cascading dropdown UI. Only `department` (the real
    FK) gets saved. Their querysets start empty/filtered and get
    re-narrowed from POSTed data in __init__ so server-side validation
    works even with JS disabled, not just the HTMX-swapped happy path.
    """

    area = forms.ModelChoiceField(queryset=Area.objects.order_by("name"), label="Area")
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label="Project")
    mine = forms.ModelChoiceField(queryset=Mine.objects.none(), label="Mine")

    class Meta:
        model = Employee
        fields = [
            "employee_code", "full_name", "designation", "date_joined",
            "phone", "email", "status", "department",
        ]
        widgets = {
            "date_joined": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        base_input_classes = (
            "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
            "focus:outline-none focus:ring-2 focus:ring-teal-500"
        )
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {base_input_classes}".strip()

        self.fields["department"].queryset = Department.objects.none()
        self.fields["department"].label = "Department"

        data = self.data if self.is_bound else None
        instance = kwargs.get("instance")

        area_id = data.get("area") if data else (
            instance.department.mine.project.area_id if instance and instance.pk else None
        )
        project_id = data.get("project") if data else (
            instance.department.mine.project_id if instance and instance.pk else None
        )
        mine_id = data.get("mine") if data else (
            instance.department.mine_id if instance and instance.pk else None
        )

        if area_id:
            self.fields["project"].queryset = Project.objects.filter(area_id=area_id).order_by("name")
        if project_id:
            self.fields["mine"].queryset = Mine.objects.filter(project_id=project_id).order_by("name")
        if mine_id:
            self.fields["department"].queryset = Department.objects.filter(mine_id=mine_id).order_by("name")

        if instance and instance.pk:
            self.fields["area"].initial = area_id
            self.fields["project"].initial = project_id
            self.fields["mine"].initial = mine_id
