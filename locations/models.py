from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Area(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)  # e.g. "Ranchi HQ"
    code = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name


class Project(TimeStampedModel):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=150)  # e.g. "Piparwar"
    code = models.CharField(max_length=20)

    class Meta:
        unique_together = [("area", "code")]

    def __str__(self):
        return f"{self.area.code}/{self.code} — {self.name}"


class Mine(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="mines")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20)

    class Meta:
        unique_together = [("project", "code")]

    def __str__(self):
        return f"{self.project.code}/{self.code} — {self.name}"


class Department(TimeStampedModel):
    mine = models.ForeignKey(Mine, on_delete=models.PROTECT, related_name="departments")
    name = models.CharField(max_length=150)
    head = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="departments_headed",
    )

    class Meta:
        unique_together = [("mine", "name")]

    def __str__(self):
        return f"{self.mine.name} / {self.name}"
