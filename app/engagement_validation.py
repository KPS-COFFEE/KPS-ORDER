from pydantic import BaseModel, ConfigDict, field_validator


class EngagementModel(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

    @field_validator('*',mode='before')
    @classmethod
    def text_storage_safe(cls,value):
        values=value if isinstance(value,list) else [value]
        if any(isinstance(item,str) and '\x00' in item for item in values):
            raise ValueError('NUL is not permitted in text')
        return value
