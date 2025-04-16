import os
import json
import requests
import base64
import threading
import time
from flask import Flask, request, jsonify
import google.generativeai as genai
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# 필요한 API 키와 설정 가져오기
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.com")  # 기본값 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)

# Flask 앱 초기화
app = Flask(__name__)

# GitLab 헤더 설정
GITLAB_HEADERS = {"Private-Token": GITLAB_TOKEN, "Content-Type": "application/json"}

# 요청 타임아웃 설정 (초)
REQUEST_TIMEOUT = 10

# 봇 식별을 위한 특별한 문자열 (댓글에 자동으로 추가됨)
BOT_SIGNATURE = "🤖 AI 코드 리뷰"


def get_mr_changes(project_id, mr_iid):
    """
    GitLab API를 사용하여 MR의 변경 사항을 가져옴.

    Args:
        project_id: GitLab 프로젝트 ID
        mr_iid: MR의 내부 ID

    Returns:
        변경된 파일 목록과 각 파일의 diff 정보
    """
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/changes"

    try:
        # 타임아웃 설정 추가
        response = requests.get(url, headers=GITLAB_HEADERS, timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            print(f"GitLab API 호출 실패: {response.status_code} - {response.text}")
            return None

        return response.json()
    except requests.exceptions.Timeout:
        print(f"GitLab API 요청 타임아웃: project_id={project_id}, mr_iid={mr_iid}")
        return None
    except Exception as e:
        print(f"GitLab API 요청 예외 발생: {str(e)}")
        return None


def get_file_content(project_id, commit_sha, file_path):
    """
    특정 커밋 파일 내용 가져오기.

    Args:
        project_id: GitLab 프로젝트 ID
        commit_sha: 파일을 가져올 커밋의 SHA
        file_path: 파일 경로

    Returns:
        파일 내용 (디코딩된 텍스트)
    """
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/repository/files/{requests.utils.quote(file_path, safe='')}/raw"
    params = {"ref": commit_sha}

    try:
        response = requests.get(
            url, headers=GITLAB_HEADERS, params=params, timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            print(f"파일 내용 가져오기 실패: {response.status_code} - {response.text}")
            return None

        return response.text
    except requests.exceptions.Timeout:
        print(f"파일 내용 가져오기 타임아웃: {file_path}")
        return None
    except Exception as e:
        print(f"파일 내용 가져오기 예외 발생: {str(e)}")
        return None


def analyze_with_gemini(changes_data, mr_info, user_prompt=None):
    """
    잼민이야 해줘.

    Args:
        changes_data: MR의 변경 사항 데이터
        mr_info: MR에 대한 기본 정보
        user_prompt: 사용자가 추가한 프롬프트 (선택적)

    Returns:
        Gemini API의 코드 리뷰 분석 결과
    """
    start_time = time.time()
    model = genai.GenerativeModel("gemini-2.0-flash")

    # 프롬프트 구성
    prompt = f"""
    다음 GitLab Merge Request의 코드 변경 사항을 시니어 개발자가 해준다는 느낌으로 분석하고 리뷰해주세요:
    
    MR 제목: {mr_info.get('title', '제목 없음')}
    MR 설명: {mr_info.get('description', '설명 없음')}
    """

    # 사용자 요청 프롬프트가 있는 경우 추가
    if user_prompt:
        prompt += f"""
    사용자 요청: {user_prompt}
    """

    prompt += """
    변경된 파일:
    """

    # 변경된 각 파일에 대한 정보 추가
    for change in changes_data.get("changes", []):
        file_path = change.get("new_path", change.get("old_path", "알 수 없는 파일"))

        # 파일 확장자 확인 (코드 파일만 분석)
        code_extensions = [
            ".py",
            ".js",
            ".java",
            ".cpp",
            ".c",
            ".h",
            ".cs",
            ".go",
            ".rb",
            ".php",
            ".ts",
            ".kt",
            ".swift",
        ]
        file_ext = os.path.splitext(file_path)[1].lower()

        if file_ext not in code_extensions:
            continue

        prompt += f"\n\n파일: {file_path}\n"
        prompt += f"변경사항:\n{change.get('diff', '변경사항 없음')}\n"

    prompt += """
    위 코드 변경사항에 대하여
    코드 품질, 잠재적 문제점, 성능 고려사항, 개선 제안
    을 간략하게 대략 500 ~ 700자 안으로 구체적인 코드 부분과 함께 분석해주세요.
    """

    try:
        response = model.generate_content(prompt)
        print(f"Gemini API 분석 소요 시간: {time.time() - start_time:.2f}초")
        return response.text
    except Exception as e:
        print(f"Gemini API 호출 실패: {str(e)}")
        print(f"Gemini API 분석 소요 시간 (실패): {time.time() - start_time:.2f}초")
        return f"코드 분석 중 오류가 발생했습니다: {str(e)}"


def analyze_with_gemini_for_comment(changes_data, mr_info, user_prompt=None):
    """
    댓글용으로 수정된 프롬프트.

    Args:
        changes_data: MR의 변경 사항 데이터
        mr_info: MR에 대한 기본 정보
        user_prompt: 사용자가 추가한 프롬프트 (선택적)

    Returns:
        Gemini API의 코드 리뷰 분석 결과
    """
    start_time = time.time()
    model = genai.GenerativeModel("gemini-2.0-flash")

    # 프롬프트 구성
    prompt = f"""
    다음 GitLab Merge Request의 코드 변경 사항 중 제가 원하는 부분만 시니어 개발자가 해준다는 느낌으로 분석하고 리뷰해주세요:
    
    MR 제목: {mr_info.get('title', '제목 없음')}
    MR 설명: {mr_info.get('description', '설명 없음')}
    """

    # 사용자 요청 프롬프트가 있는 경우 추가
    if user_prompt:
        prompt += f"""
    사용자 요청: {user_prompt}
    """

    prompt += """
    변경된 파일:
    """

    # 변경된 각 파일에 대한 정보 추가
    for change in changes_data.get("changes", []):
        file_path = change.get("new_path", change.get("old_path", "알 수 없는 파일"))

        # 파일 확장자 확인 (코드 파일만 분석)
        code_extensions = [
            ".py",
            ".js",
            ".java",
            ".cpp",
            ".c",
            ".h",
            ".cs",
            ".go",
            ".rb",
            ".php",
            ".ts",
            ".kt",
            ".swift",
        ]
        file_ext = os.path.splitext(file_path)[1].lower()

        if file_ext not in code_extensions:
            continue

        prompt += f"\n\n파일: {file_path}\n"
        prompt += f"변경사항:\n{change.get('diff', '변경사항 없음')}\n"

    prompt += """
    위 코드 변경사항에 제가 원하는 부분을을
    간략하게 대략 300자 안으로 분석해주세요.
    """

    try:
        response = model.generate_content(prompt)
        print(f"Gemini API 분석 소요 시간: {time.time() - start_time:.2f}초")
        return response.text
    except Exception as e:
        print(f"Gemini API 호출 실패: {str(e)}")
        print(f"Gemini API 분석 소요 시간 (실패): {time.time() - start_time:.2f}초")
        return f"코드 분석 중 오류가 발생했습니다: {str(e)}"


def post_comment_to_mr(project_id, mr_iid, comment):
    """
    MR 댓글 프롬프트.

    Args:
        project_id: GitLab 프로젝트 ID
        mr_iid: MR의 내부 ID
        comment: 작성할 댓글 내용

    Returns:
        성공 여부 (Boolean)
    """
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
    data = {"body": comment}

    # 디버깅을 위한 로깅 추가
    print(f"댓글 작성 요청 URL: {url}")
    print(f"요청 헤더: {GITLAB_HEADERS}")
    print(f"요청 데이터 길이: {len(str(data))} 바이트")

    try:
        response = requests.post(
            url, headers=GITLAB_HEADERS, json=data, timeout=REQUEST_TIMEOUT
        )

        # 응답 내용 상세 로깅
        print(f"댓글 작성 응답 코드: {response.status_code}")
        print(f"댓글 작성 응답 헤더: {response.headers}")
        print(
            f"댓글 작성 응답 내용: {response.text[:200]}..."
        )  # 응답이 길 수 있어 일부만 출력

        if response.status_code not in [200, 201]:
            print(f"댓글 작성 실패: {response.status_code} - {response.text}")
            return False

        return True
    except requests.exceptions.Timeout:
        print(f"댓글 작성 타임아웃: project_id={project_id}, mr_iid={mr_iid}")
        return False
    except Exception as e:
        print(f"댓글 작성 예외 발생: {str(e)}")
        return False


def process_mr_in_background(data):
    """
    비동기 댓글 처리.

    Args:
        data: GitLab 웹훅으로 받은 데이터
    """
    # 처리할 MR 상태 확인 (opened 또는 updated만 처리)
    action = data.get("object_attributes", {}).get("action")
    if action not in ["open", "update"]:
        print(f"처리하지 않는 MR 액션: {action}")
        return

    # MR 정보 추출
    mr_attrs = data.get("object_attributes", {})
    project_id = mr_attrs.get("target_project_id")
    mr_iid = mr_attrs.get("iid")

    print(f"백그라운드에서 MR 처리 시작: project_id={project_id}, mr_iid={mr_iid}")

    # MR 변경 사항 가져오기
    start_time = time.time()
    changes_data = get_mr_changes(project_id, mr_iid)
    print(f"MR 변경 사항 가져오기 소요 시간: {time.time() - start_time:.2f}초")

    if not changes_data:
        print(f"MR {mr_iid} 변경 사항을 가져올 수 없습니다.")
        return

    # MR 정보 구성
    mr_info = {
        "title": mr_attrs.get("title", ""),
        "description": mr_attrs.get("description", ""),
    }

    # Gemini API로 코드 분석
    analysis_result = analyze_with_gemini(changes_data, mr_info)

    # 분석 결과를 MR에 댓글로 작성
    comment = f"""
## {BOT_SIGNATURE}

{analysis_result}

---
*이 리뷰는 자동으로 생성되었습니다. [Git](https://github.com/PMRAZOR/Gitlab-MR-Review-Bot)*
"""

    start_time = time.time()
    success = post_comment_to_mr(project_id, mr_iid, comment)
    print(f"댓글 작성 소요 시간: {time.time() - start_time:.2f}초")

    if success:
        print(f"MR {mr_iid}에 대한 코드 리뷰가 성공적으로 작성되었습니다.")
    else:
        print(f"MR {mr_iid}에 대한 코드 리뷰 댓글 작성에 실패했습니다.")

    print(f"백그라운드 처리 완료: project_id={project_id}, mr_iid={mr_iid}")


def process_note_in_background(data):
    """
    백그라운드에서 Note(댓글)처리 ( 깃랩은 전부 Note로 인식 ).
    """
    # 노트 정보 추출
    note = data.get("object_attributes", {})
    note_id = note.get("id")
    note_body = note.get("note", "")
    project_id = data.get("project", {}).get("id")

    # 노트가 달린 대상 타입과 ID 확인
    noteable_type = note.get("noteable_type")
    noteable_id = note.get("noteable_id")
    mr_iid = None

    # 웹훅 데이터 구조를 더 자세히 로깅
    print(f"Note Hook 전체 데이터: {json.dumps(data)[:1000]}...")

    # MR 정보를 다양한 위치에서 찾기 시도
    if noteable_type == "MergeRequest":
        # 방법 1: 기본 경로
        mr_iid = note.get("noteable_iid")

        # 방법 2: 중첩된 MR 객체 확인
        if mr_iid is None and "merge_request" in data:
            mr_iid = data.get("merge_request", {}).get("iid")

        # 방법 3: 다른 경로 시도
        if mr_iid is None:
            try:
                # MR ID를 사용하여 API로 IID 조회
                url = f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests"
                response = requests.get(
                    url, headers=GITLAB_HEADERS, timeout=REQUEST_TIMEOUT
                )

                if response.status_code == 200:
                    mrs = response.json()
                    for mr in mrs:
                        if mr.get("id") == noteable_id:
                            mr_iid = mr.get("iid")
                            break
            except Exception as e:
                print(f"MR IID 조회 실패: {str(e)}")

    print(
        f"노트 처리 시작: project_id={project_id}, noteable_type={noteable_type}, noteable_id={noteable_id}, mr_iid={mr_iid}, note_id={note_id}"
    )

    # MR IID가 여전히 없으면 처리 중단
    if noteable_type != "MergeRequest" or mr_iid is None:
        print(
            f"MR에 달린 댓글이 아니거나 MR IID를 찾을 수 없습니다: noteable_type={noteable_type}, mr_iid={mr_iid}"
        )
        return

    # 작성자 정보
    author = data.get("user", {})
    author_username = author.get("username", "")

    print(f"작성자: {author_username}, 내용: {note_body[:100]}...")

    # 봇이 생성한 댓글인지 확인 (무한 루프 방지)
    if BOT_SIGNATURE in note_body:
        print(f"봇이 생성한 댓글입니다. 처리를 건너뜁니다.")
        return

    # 사용자 프롬프트 추출
    user_prompt = None
    command_triggers = ["@bot", "/review", "/analyze"]

    for trigger in command_triggers:
        if trigger in note_body.lower():
            # 트리거 이후의 텍스트를 사용자 프롬프트로 추출
            trigger_index = note_body.lower().find(trigger)
            if trigger_index != -1:
                # 트리거 다음 텍스트 추출 (트리거 길이 + 공백 고려)
                user_prompt = note_body[trigger_index + len(trigger) :].strip()
                print(f"사용자 프롬프트 추출: '{user_prompt}'")
                break

    # 더 간단한 트리거도 확인 (코드리뷰, 코드 리뷰, 리뷰)
    simple_triggers = ["코드리뷰", "코드 리뷰", "리뷰"]
    if user_prompt is None:
        for trigger in simple_triggers:
            if trigger in note_body.lower():
                # 간단한 트리거가 있을 경우 전체 내용을 프롬프트로 사용
                user_prompt = note_body.strip()
                print(f"간단한 트리거로 프롬프트 추출: '{user_prompt}'")
                break

    # 명령어가 감지되면 코드 리뷰 수행
    if user_prompt is not None:
        print(f"댓글에서 명령어 감지됨, 코드 리뷰 시작")

        # MR 변경 사항 가져오기
        changes_data = get_mr_changes(project_id, mr_iid)

        if not changes_data:
            print(f"MR {mr_iid} 변경 사항을 가져올 수 없습니다.")
            response = "변경 사항을 가져올 수 없어 코드 리뷰를 수행할 수 없습니다."
            post_comment_to_mr(project_id, mr_iid, response)
            return

        # MR 정보 가져오기 (별도 API 호출 필요)
        try:
            url = f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}"
            response = requests.get(
                url, headers=GITLAB_HEADERS, timeout=REQUEST_TIMEOUT
            )
            mr_data = response.json() if response.status_code == 200 else {}

            mr_info = {
                "title": mr_data.get("title", "제목 없음"),
                "description": mr_data.get("description", "설명 없음"),
            }
        except Exception as e:
            print(f"MR 정보 가져오기 실패: {str(e)}")
            mr_info = {"title": "제목 없음", "description": "설명 없음"}

        # 코드 분석 수행 (사용자 프롬프트 추가)
        print(f"사용자 요청에 의한 코드 리뷰 시작: MR #{mr_iid}")
        analysis_result = analyze_with_gemini_for_comment(
            changes_data, mr_info, user_prompt
        )

        # 분석 결과를 MR에 댓글로 작성
        comment = f"""
## {BOT_SIGNATURE} - 사용자 요청

{analysis_result}

---
*이 리뷰는 @{author_username}님의 요청에 의해 생성되었습니다. [Git](https://github.com/PMRAZOR/Gitlab-MR-Review-Bot)*
"""

        success = post_comment_to_mr(project_id, mr_iid, comment)

        if success:
            print(f"사용자 요청에 의한 코드 리뷰가 성공적으로 작성되었습니다.")
        else:
            print(f"사용자 요청에 의한 코드 리뷰 댓글 작성에 실패했습니다.")

    else:
        print(f"처리할 명령어가 감지되지 않았습니다. 댓글 무시: {note_body[:50]}...")

    print(
        f"노트 처리 완료: project_id={project_id}, mr_iid={mr_iid}, note_id={note_id}"
    )


@app.route("/test", methods=["GET", "POST"])
def test_endpoint():
    return (
        jsonify(
            {
                "status": "success",
                "method": request.method,
                "message": "Test endpoint is working",
            }
        ),
        200,
    )


@app.route("/webhook/gitlab", methods=["POST", "GET"])
def gitlab_webhook():
    print(f"Received request: Method={request.method}, Headers={request.headers}")

    if request.method == "GET":
        return (
            jsonify({"status": "success", "message": "Webhook endpoint is active"}),
            200,
        )

    try:
        data = request.json
        # 데이터 일부 출력 (디버깅용)
        data_preview = (
            json.dumps(data)[:500] + "..."
            if len(json.dumps(data)) > 500
            else json.dumps(data)
        )
        print(f"요청 데이터 미리보기: {data_preview}")
    except Exception as e:
        print(f"요청 데이터 파싱 실패: {str(e)}")
        return jsonify({"status": "error", "message": "잘못된 JSON 형식"}), 400

    # 이벤트 유형 확인
    event_type = request.headers.get("X-Gitlab-Event", "알 수 없음")
    object_kind = data.get("object_kind", "알 수 없음")
    print(f"이벤트 유형: {event_type}, Object Kind: {object_kind}")

    # 이벤트 유형에 따라 처리
    if object_kind == "merge_request":
        # MR 이벤트 처리
        print("MR 이벤트 감지됨 - 코드 리뷰 시작")
        # 실제 처리는 별도 스레드에서 진행 (타임아웃 방지)
        threading.Thread(target=process_mr_in_background, args=(data,)).start()

        return (
            jsonify(
                {
                    "status": "accepted",
                    "message": "MR 요청이 접수되었습니다. 백그라운드에서 처리됩니다.",
                }
            ),
            202,
        )

    elif object_kind == "note":
        # Note Hook 이벤트 처리
        print("Note Hook 이벤트 감지됨 - 댓글 처리")
        # 실제 처리는 별도 스레드에서 진행
        threading.Thread(target=process_note_in_background, args=(data,)).start()

        return (
            jsonify(
                {
                    "status": "accepted",
                    "message": "노트 요청이 접수되었습니다. 백그라운드에서 처리됩니다.",
                }
            ),
            202,
        )

    else:
        print(f"처리하지 않는 이벤트 유형: {object_kind}")
        return (
            jsonify(
                {
                    "status": "ignored",
                    "message": f"처리하지 않는 이벤트 유형: {object_kind}",
                }
            ),
            200,
        )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
