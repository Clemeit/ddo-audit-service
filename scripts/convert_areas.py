import csv
from typing import Optional
from pydantic import BaseModel
import json

import os

# set working directory to the root of the project so that the script can use pydantic
os.chdir(os.path.join(os.path.dirname(__file__), ".."))


class Area(BaseModel):
    id: int
    name: str
    is_public: Optional[bool] = True
    is_wilderness: Optional[bool] = False
    region: Optional[str] = None


def __main__():
    fields = ["id", "name", "is_public", "region"]
    areas: list[dict] = []
    with open("./areas.csv", "r") as csvfile:
        reader = csv.DictReader(csvfile, fieldnames=fields)
        for row in reader:
            areas.append(Area(**row).model_dump())
    output = json.dumps(areas, indent=2)
    # save output to file
    with open("./areas.json", "w") as f:
        f.write(output)


if __name__ == "__main__":
    __main__()
