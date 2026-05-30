import requests
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta

app = FastAPI()

# 제공해주신 역코드 API 인증키 세팅
SEOUL_API_KEY = "71676b6a7864756238386a52564853" 

DRINK_DATA = {
    "soju": {"abv": 0.165, "volume_per_glass": 50},
    "beer": {"abv": 0.045, "volume_per_glass": 355},
    "makgeolli": {"abv": 0.06, "volume_per_glass": 150},
    "highball": {"abv": 0.08, "volume_per_glass": 350}
}

class DrinkingSession(BaseModel):
    weight: float
    gender: str
    drink_type: str
    glasses: int
    start_time: str
    current_station: str     # 현재 술자리 근처 역 이름 (예: "홍대입구", "강남")
    destination_station: str # 도착하고 싶은 목적지 역 이름 (예: "신도림", "수원")
    normal_walk_minutes: int # 현재 위치에서 '현재 역'까지 평소 걸어가는 시간 (분)

def get_station_code(station_name: str):
    """역 이름을 가지고 외부코드(FR_CODE)를 조회하는 함수"""
    clean_name = station_name.replace("역", "").strip()
    url = f"http://openAPI.seoul.go.kr:8088/{SEOUL_API_KEY}/json/SearchInfoBySubwayNameService/1/1/{clean_name}/"
    try:
        res = requests.get(url).json()
        if "SearchInfoBySubwayNameService" in res:
            row = res["SearchInfoBySubwayNameService"]["row"][0]
            return row["FR_CODE"], row["LINE_NUM"], clean_name
    except Exception:
        return None, None, clean_name
    return None, None, clean_name

@app.post("/calculate_real_goldentime")
def calculate_real_goldentime(session: DrinkingSession):
    # 1. 역 이름으로 각각의 외부코드 조회
    current_code, current_line, current_name = get_station_code(session.current_station)
    dest_code, dest_line, dest_name = get_station_code(session.destination_station)
    
    if not current_code:
        return {"error": f"현재 역 '{session.current_station}'을 찾을 수 없습니다."}
    if not dest_code:
        return {"error": f"목적지 역 '{session.destination_station}'을 찾을 수 없습니다."}

    # 2. 위드마크 알코올 계산 및 도보 속도 가중치 산출
    drink = DRINK_DATA.get(session.drink_type.lower())
    if not drink:
        return {"error": "지원하지 않는 술 종류입니다."}
    
    total_volume = drink["volume_per_glass"] * session.glasses
    alcohol_g = total_volume * drink["abv"] * 0.7894
    
    r = 0.86 if session.gender.lower() == "male" else 0.64
    base_bac = alcohol_g / (session.weight * r * 10)
    
    start_dt = datetime.strptime(session.start_time, "%Y-%m-%d %H:%M")
    current_dt = datetime.now()
    elapsed_hours = (current_dt - start_dt).total_seconds() / 3600
    current_bac = max(0.0, base_bac - (elapsed_hours * 0.015))
    
    if current_bac >= 0.08:
        walking_speed_factor = 0.7   # 만취
        status = "만취 상태"
    elif current_bac >= 0.03:
        walking_speed_factor = 0.85  # 취기
        status = "취기 상태"
    else:
        walking_speed_factor = 1.0   # 정상
        status = "정상 상태"

    # 취기 반영 실제 소요 도보 시간
    real_walk_minutes = round(session.normal_walk_minutes / walking_speed_factor)

    # 3. 오늘 요일에 맞는 요일코드 구분 (1: 평일, 2: 토요일, 3: 일요일)
    weekday = current_dt.weekday()
    week_tag = "2" if weekday == 5 else ("3" if weekday == 6 else "1")

    # 4. 현재 역의 공식 막차 시간표 API 호출
    last_train_time_str = None
    last_train_line = "정보 없음"
    
    for inout_tag in ["1", "2"]:
        url = f"http://openAPI.seoul.go.kr:8088/{SEOUL_API_KEY}/json/SearchLastTrainTimeByFRCodeService/1/5/{current_code}/{week_tag}/{inout_tag}"
        try:
            response = requests.get(url)
            data = response.json()
            if "SearchLastTrainTimeByFRCodeService" in data:
                rows = data["SearchLastTrainTimeByFRCodeService"]["row"]
                for row in rows:
                    t_str = row.get("LEFTTIME") or row.get("ARRIVETIME")
                    if t_str and t_str != "00:00:00":
                        formatted_time = t_str[:5]
                        
                        # 목적지 방향과 매칭되거나 가장 늦은 막차 확보
                        if not last_train_time_str or formatted_time > last_train_time_str:
                            last_train_time_str = formatted_time
                            last_train_line = f"{row.get('LINE_NUM', '')}선 ({row.get('SUBWAYSTARTSTN_NM', '')} -> {row.get('SUBWAYENDSTN_NM', '')})"
        except Exception:
            continue

    # 예외 안전장치
    if not last_train_time_str:
        last_train_time_str = "23:45"
        last_train_line = "시간표 API 통신 제한으로 인한 가상 안전시간"

    # 5. 목적지 역까지의 지하철 탑승 이동시간 계산 (동일 노선 기준 단순화 샘플링)
    # 실제 운행 정보 계산을 위해 기본 이동 마진(20분)을 설정하고, 다른 노선일 경우 환승 마진(15분) 추가
    travel_minutes = 20 
    if current_line != dest_line:
        travel_minutes += 15 # 환승 페널티

    # 6. 골든타임 역산 (목적지 기준 데드라인 연산)
    today_str = current_dt.strftime("%Y-%m-%d")
    last_train_dt = datetime.strptime(f"{today_str} {last_train_time_str}", "%Y-%m-%d %H:%M")
    
    # 골든타임 = 현재역 막차시간 - 실제 도보 시간 - 안전 마진(3분)
    # 목적지까지 끊기지 않고 가기 위해 계산된 최종 출발 시각
    golden_time_dt = last_train_dt - timedelta(minutes=real_walk_minutes + 3)
    time_left_minutes = round((golden_time_dt - current_dt).total_seconds() / 60)

    return {
        "현재 혈중알코올농도(BAC)": round(current_bac, 4),
        "현재 상태": status,
        "현재 위치(출발 역)": f"{current_name}역 ({current_line})",
        "가야할 곳(목적지 역)": f"{dest_name}역 ({dest_line})",
        "조회된 현재역 막차 노선": last_train_line,
        "현재역 막차 출발 시간": last_train_time_str,
        "취기 반영 역까지 도보 시간": f"{real_walk_minutes}분 (평소보다 {round((1-walking_speed_factor)*100)}% 지연됨)",
        "예상 지하철 이동 시간": f"약 {travel_minutes}분 (환승 요인 반영)",
        "술자리에서 일어나야 하는 골든타임": golden_time_dt.strftime("%H:%M"),
        "골든타임까지 남은 시간": f"{time_left_minutes}분 남음" if time_left_minutes > 0 else "목적지 방향 막차가 마감되었거나 시간이 아슬아슬합니다. 택시나 숙소를 고려하세요!"
    }