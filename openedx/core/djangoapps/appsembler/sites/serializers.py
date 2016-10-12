from django.contrib.sites.models import Site
from rest_framework import serializers
from organizations import api as organizations_api
from organizations.models import Organization
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from .utils import sass_to_dict, dict_to_sass, bootstrap_site


class SASSDictField(serializers.DictField):
    def to_internal_value(self, data):
        return dict_to_sass(data)

    def to_representation(self, value):
        return sass_to_dict(value)


class SiteConfigurationSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='site.name', read_only=True)
    domain = serializers.CharField(source='site.domain', read_only=True)
    values = serializers.DictField()
    sass_variables = SASSDictField()
    page_elements = serializers.DictField()

    class Meta:
        model = SiteConfiguration
        fields = ('id', 'name', 'domain', 'values', 'sass_variables', 'page_elements')

    def update(self, instance, validated_data):
        object = super(SiteConfigurationSerializer, self).update(instance, validated_data)
        # TODO: make this per-site, not scalable in production
        Site.objects.clear_cache()
        return object


class SiteConfigurationListSerializer(SiteConfigurationSerializer):
    class Meta(SiteConfigurationSerializer.Meta):
        fields = ('id', 'name', 'domain')


class SiteSerializer(serializers.ModelSerializer):
    configuration = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Site
        fields = ('id', 'name', 'domain', 'configuration')

    def create(self, validated_data):
        site = super(SiteSerializer, self).create(validated_data)
        site = bootstrap_site(site)
        return site


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ('id', 'name', 'short_name')

    def create(self, validated_data):
        return organizations_api.add_organization(**validated_data)


class RegistrationSerializer(serializers.Serializer):
    site = SiteSerializer()
    organization = OrganizationSerializer()

    def create(self, validated_data):
        site_data = validated_data.pop('site')
        site = Site.objects.create(**site_data)
        organization_data = validated_data.pop('organization')
        organization, _ = bootstrap_site(site, organization_data.get('name'))
        return {
            'site': site,
            'organization': organization
        }
