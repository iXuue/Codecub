from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ApiConnectionProfile:
    id: str
    display_name: str
    connection_type: str
    api_operator: str
    endpoint_origin: str
    base_url: str
    protocol: str
    response_schema: str
    model_vendor: str
    credential_id: str
    endpoint_verification_status: str = "unverified"
    usage_schema_verification_status: str = "unverified"
    prompt_cache_request_mode: str = "unavailable"
    prompt_cache_request_support_status: str = "unverified"

    @property
    def verification_status(self):
        """Compatibility alias for old callers; endpoint identity only."""
        return self.endpoint_verification_status

    def to_dict(self):
        return asdict(self)
