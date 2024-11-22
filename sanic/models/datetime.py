from pydantic import BaseModel


class MyClass(BaseModel):
    timestamp: float


# class CustomDateTime(BaseModel):
#     """
#     This model will be used to handle datetime objects in such a way that
#     they can be serialized to JSON.
#     """

#     dt: datetime

#     @model_serializer()
#     def serialize_model(self):
#         return self.dt.isoformat()

#     @classmethod
#     def now(cls):
#         return cls(dt=datetime.now())

#     def __str__(self):
#         return self.dt.isoformat()

#     @model_validator(mode="before")
#     def parse_dt(cls, v):
#         if isinstance(v, str):
#             return cls.model_construct(dt=datetime.fromisoformat(v))
#         return v


# class CustomDateTime(BaseModel):
#     """
#     This model will be used to handle datetime objects in such a way that
#     they can be serialized to JSON.
#     """

#     value: datetime

#     @model_validator(mode="before")
#     def validate_datetime(cls, values):
#         if isinstance(values, dict) and isinstance(values.get("value"), str):
#             values["value"] = datetime.fromisoformat(values["value"])
#         return values

#     @model_serializer()
#     def serialize_model(self):
#         return self.value.isoformat()

#     @classmethod
#     def now(cls):
#         return cls(dt=datetime.now())

#     def __str__(self):
#         return self.value.isoformat()

#     class Config:
#         arbitrary_types_allowed = True


# class MyClass(BaseModel):
#     dt: CustomDateTime

#     # allow string to be passed in to dt:
#     @model_validator(mode="before")
#     def validate_datetime(cls, values):
#         if isinstance(values, dict) and isinstance(values.get("dt"), str):
#             values["dt"] = {"value": values["dt"]}
#         return values


# print()
# # print(CustomDateTime.now())
# print(MyClass(dt="2024-11-20T14:06:23.425638").model_dump())
