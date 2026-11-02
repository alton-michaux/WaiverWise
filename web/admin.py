from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

User = get_user_model()


class WaiverWiseUserAdmin(UserAdmin):
    """WaiverWise has no email, so a lost password means sending someone a link by hand.

    It's made here rather than by a command because the link is signed with the live server's SECRET_KEY.
    """

    actions = ["password_reset_link"]

    @admin.action(description="Make a password reset link", permissions=["change"])
    def password_reset_link(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Pick one user at a time.", messages.ERROR)
            return
        user = queryset.get()
        if user.is_superuser and not request.user.is_superuser:
            self.message_user(request, "Only a superuser can do this for a superuser.", messages.ERROR)
            return
        path = reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)])
        self.message_user(request, f"Link for {user.username}, good for 3 days or until it's used: {request.build_absolute_uri(path)}", messages.WARNING)


admin.site.unregister(User)
admin.site.register(User, WaiverWiseUserAdmin)
