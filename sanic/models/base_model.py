from pydantic import BaseModel
from datetime import datetime


# TODO: this is stupid
class ConfiguredBaseModel(BaseModel):
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    # TODO: this won't work because it's not recursive
    def model_dump(self, **kwargs):
        data = super().model_dump(**kwargs)
        for field, value in data.items():
            print(f"Field: {field}, Value: {value}")
            if isinstance(value, datetime):
                print("true")
                data[field] = self.Config.json_encoders[datetime](value)
        return data
