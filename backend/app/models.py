from pydantic import BaseModel, Field

class DriveCommand(BaseModel):
    direction: str = Field(pattern="^(forward|backward|left|right)$")
    speed: int = Field(ge=0, le=100, description="Motor output 0-100 (library PWM or direct duty, see drive settings)")
    steering: int = Field(ge=-30, le=30, description="Signed steering angle in degrees")

class TimedDrive(BaseModel):
    direction: str = Field(pattern="^(forward|backward)$")
    speed: int = Field(ge=1, le=100)
    steering: int = Field(default=0, ge=-30, le=30)
    seconds: float = Field(ge=0.2, le=30, description="이 시간만큼 달린 뒤 자동 정지")

class CalibrationUpdate(BaseModel):
    steering_center: float | None = Field(default=None, ge=-20, le=20)
    pan_center: int | None = Field(default=None, ge=-30, le=30)
    tilt_center: int | None = Field(default=None, ge=-30, le=30)

class SteeringTest(BaseModel):
    direction: str = Field(pattern="^(left|right|center)$")
    angle: int = Field(default=20, ge=0, le=30)

class AutonomyStart(BaseModel):
    mode: str = Field(pattern="^(manual|line_following|obstacle_avoidance|object_following|camera_tracking|autonomous)$")


class DriveSettingsUpdate(BaseModel):
    mapping: str | None = Field(default=None, pattern="^(library|duty)$", description="library: picarx forward/backward (value/2+50 %), duty: direct duty %")
    trim_forward: float | None = Field(default=None, ge=0.5, le=1.5)
    trim_backward: float | None = Field(default=None, ge=0.5, le=1.5)
    trim_left: float | None = Field(default=None, ge=0.5, le=1.5, description="왼쪽 바퀴(모터 1) 출력 배율")
    trim_right: float | None = Field(default=None, ge=0.5, le=1.5, description="오른쪽 바퀴(모터 2) 출력 배율")

class CameraView(BaseModel):
    pan: float | None = Field(default=None, ge=-90, le=90)
    tilt: float | None = Field(default=None, ge=-90, le=90)
    pan_delta: float | None = Field(default=None, ge=-20, le=20)
    tilt_delta: float | None = Field(default=None, ge=-20, le=20)
