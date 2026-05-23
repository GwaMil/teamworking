from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI()

# 술 종류별 알코올 도수 및 1잔당 용량(ml) 기준 데이터베이스
DRINK_DATA = {
    "soju": {"abv": 0.165, "volume_per_glass": 50},    # 소주 (16.5%, 1잔 50ml)
    "beer": {"abv": 0.045, "volume_per_glass": 355},   # 맥주 (4.5%, 1캔/잔 355ml)
    "makgeolli": {"abv": 0.06, "volume_per_glass": 150}, # 막걸리 (6%, 1잔 150ml)
    "highball": {"abv": 0.08, "volume_per_glass": 350}  # 하이볼 (8%, 1잔 350m)
}

# 사용자로부터 받을 데이터 양식 정의
class DrinkingSession(BaseModel):
    weight: float          # 체중 (kg)
    gender: str            # 성별 ("male" 또는 "female")
    drink_type: str        # 술 종류 ("soju", "beer", "makgeolli", "highball")
    glasses: int           # 마신 잔(또는 병/캔) 수
    start_time: str        # 음주 시작 시간 (형식: "YYYY-MM-DD HH:MM")

@app.get("/")
def read_root():
    return {"message": "술자리 귀가 메이트 서버가 정상 작동 중입니다!"}

@app.post("/calculate_real_bac")
def calculate_real_bac(session: DrinkingSession):
    # 1. 술 종류 변환 및 총 알코올 양(g) 계산
    # 알코올 질량(g) = 음주량(ml) * (도수/100) * 알코올 비중(0.7894)
    drink = DRINK_DATA.get(session.drink_type.lower())
    if not drink:
        return {"error": "지원하지 않는 술 종류입니다. (soju, beer, makgeolli, highball 중 선택)"}
    
    total_volume = drink["volume_per_glass"] * session.glasses
    alcohol_g = total_volume * drink["abv"] * 0.7894
    
    # 2. 위드마크 기본 공식 적용 (성별 계수: 남 0.86, 여 0.64)
    r = 0.86 if session.gender.lower() == "male" else 0.64
    base_bac = alcohol_g / (session.weight * r * 10)
    
    # 3. 시간 경과에 따른 알코올 분해 반영 (-0.015% / 시간)
    try:
        start_dt = datetime.strptime(session.start_time, "%Y-%m-%d %H:%M")
        current_dt = datetime.now()
        elapsed_hours = (current_dt - start_dt).total_seconds() / 3600
    except ValueError:
        return {"error": "시간 형식이 올바르지 않습니다. 'YYYY-MM-DD HH:MM' 형식으로 입력해주세요."}
    
    # 경과 시간만큼 알코올 감소 (음수가 되지 않도록 대조)
    bac_reduction = elapsed_hours * 0.015
    current_bac = max(0.0, base_bac - bac_reduction)
    
    # 4. 혈중알코올농도에 따른 '보행 속도 가중치' 도출
    # 정상 보행 속도를 1.0이라고 했을 때, 취할수록 느려지는 비율
    if current_bac >= 0.08:
        walking_speed_factor = 0.7  # 평소보다 30% 느리게 걸음 (만취)
        status = "만취 상태 (인사불성, 즉시 귀가 필요)"
    elif current_bac >= 0.03:
        walking_speed_factor = 0.85 # 평소보다 15% 느리게 걸음 (취기)
        status = "면허 정지 수준 (판단력 저하 시작)"
    else:
        walking_speed_factor = 1.0  # 정상 속도
        status = "정상 또는 경미한 취기"

    return {
        "음주 시작 시간": session.start_time,
        "현재 시간": current_dt.strftime("%Y-%m-%d %H:%M"),
        "술자리 경과 시간(시간)": round(elapsed_hours, 1),
        "총 섭취한 알코올(g)": round(alcohol_g, 2),
        "현재 예상 혈중알코올농도(BAC)": round(current_bac, 4),
        "상태 상태": status,
        "추천 반영 보행 속도 비율": walking_speed_factor
    }
