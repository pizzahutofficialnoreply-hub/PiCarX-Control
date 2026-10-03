from pydantic import BaseModel, Field

class DriveCommand(BaseModel):
    direction: str = Field(pattern="^(forward|backward|left|right)$")
    speed: int = Field(ge=0, le=60, description="Motor PWM percentage, capped at 60")
    steering: int = Field(ge=-30, le=30, description="Signed steering angle in degrees")

class CalibrationUpdate(BaseModel):
    steering_center: int | None = Field(default=None, ge=-20, le=20)
    pan_center: int | None = Field(default=None, ge=-30, le=30)
    tilt_center: int | None = Field(default=None, ge=-30, le=30)

class SteeringTest(BaseModel):
    direction: str = Field(pattern="^(left|right|center)$")
    angle: int = Field(default=20, ge=0, le=30)

class AutonomyStart(BaseModel):
    mode: str = Field(pattern="^(manual|line_following|obstacle_avoidance|object_following|camera_tracking|autonomous)$")
