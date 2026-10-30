from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class MeSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone", "display_name"]
        read_only_fields = ["username"]


class LoginSerializer(TokenObtainPairSerializer):
    """Accepts username, email or phone in the `username` field."""

    def validate(self, attrs):
        login = (attrs.get(self.username_field) or "").strip()
        match = (
            User.objects.filter(username__iexact=login).first()
            or User.objects.filter(email__iexact=login).exclude(email="").first()
            or User.objects.filter(phone=login).exclude(phone="").first()
        )
        if match:
            attrs[self.username_field] = match.username
        data = super().validate(attrs)
        if not self.user.is_superuser:
            raise serializers.ValidationError(
                {"detail": "Only the system administrator can sign in to ASK Company."})
        data["user"] = MeSerializer(self.user).data
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Current password is wrong."})
        validate_password(attrs["new_password"], user)
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField()
