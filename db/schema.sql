-- QR 안심 운송장 (seungcurity) — 시연용 MySQL 8 스키마
-- 테이블·열 이름은 server/SERVER_SPEC.md §4 약속 그대로다. 바꾸면 서버가 깨진다.
--
-- 적용:  mysql -u root -p < db/schema.sql
-- 주의:  다시 적용하면 6개 테이블을 지우고 새로 만든다(데이터는 python -m app.seed 로 다시 채운다).
-- 필요:  MySQL 8.0.16 이상 (CHECK 제약 적용)

CREATE DATABASE IF NOT EXISTS seungcurity
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE seungcurity;

SET NAMES utf8mb4;

-- 자식 → 부모 순서로 지운다 (트리거는 orders와 함께 사라진다)
DROP TABLE IF EXISTS access_logs;
DROP TABLE IF EXISTS assignments;
DROP TABLE IF EXISTS pii_archive;
DROP TABLE IF EXISTS recipient_pii;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS drivers;

-- ---------------------------------------------------------------------------
-- 1. drivers : 허브 직원·배송기사 계정
-- ---------------------------------------------------------------------------
CREATE TABLE drivers (
  driver_id     VARCHAR(10)  NOT NULL                COMMENT '예: H001, D001',
  name          VARCHAR(20)  NOT NULL                COMMENT '직원 이름 (워터마크에 쓰인다)',
  password_hash VARCHAR(200) NOT NULL                COMMENT 'pbkdf2_sha256$200000$salt$hash',
  role          VARCHAR(10)  NOT NULL                COMMENT 'HUB 또는 DRIVER',
  device_uuid   VARCHAR(64)  NULL                    COMMENT '처음 로그인한 기기 1대에 묶인다',
  PRIMARY KEY (driver_id),
  CONSTRAINT chk_drivers_role CHECK (role IN ('HUB', 'DRIVER'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='직원 계정';

-- ---------------------------------------------------------------------------
-- 2. orders : 주문(운송장). 개인정보 없음
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
  id           INT          NOT NULL AUTO_INCREMENT,
  order_no     VARCHAR(20)  NOT NULL                 COMMENT 'SC-YYYYMMDD-NNNN',
  qr_token     VARCHAR(32)  NOT NULL                 COMMENT 'QR에 인쇄되는 무작위 토큰',
  region       VARCHAR(50)  NOT NULL                 COMMENT '권역 (허브 직원이 보는 범위)',
  status       VARCHAR(20)  NOT NULL DEFAULT 'READY' COMMENT 'READY → AT_HUB → OUT_FOR_DELIVERY → DELIVERED',
  created_at   DATETIME     NOT NULL                 COMMENT 'KST',
  delivered_at DATETIME     NULL                     COMMENT 'KST, 배송 완료 시각',
  photo_path   VARCHAR(200) NULL                     COMMENT '배송 완료 사진 파일 이름',
  PRIMARY KEY (id),
  UNIQUE KEY uq_orders_order_no (order_no),
  UNIQUE KEY uq_orders_qr_token (qr_token),
  CONSTRAINT chk_orders_status
    CHECK (status IN ('READY', 'AT_HUB', 'OUT_FOR_DELIVERY', 'DELIVERED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='주문. 개인정보는 recipient_pii에 따로 둔다';

-- ---------------------------------------------------------------------------
-- 3. recipient_pii : 조회용 개인정보 (배송 중에만 존재)
--    *_enc = AES-256-GCM 암호문, base64(nonce 12바이트 + 암호문 + 태그)
--    orders와 테이블을 나눈 이유: MySQL 트리거는 자기를 실행시킨 테이블을 수정할 수 없다(오류 1442).
-- ---------------------------------------------------------------------------
CREATE TABLE recipient_pii (
  order_id    INT         NOT NULL,
  name_enc    TEXT        NOT NULL                   COMMENT '받는 사람 이름 (암호문)',
  phone_enc   TEXT        NOT NULL                   COMMENT '전화번호 (암호문)',
  address_enc TEXT        NOT NULL                   COMMENT '상세 주소 (암호문)',
  safe_number VARCHAR(20) NOT NULL                   COMMENT '안심번호 050-XXXX-XXXX',
  PRIMARY KEY (order_id),
  CONSTRAINT fk_recipient_pii_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='조회용 개인정보. 배송 완료 순간 트리거가 비운다';

-- ---------------------------------------------------------------------------
-- 4. pii_archive : 보존용 개인정보 (분리 보관, API로 복호화하지 않는다)
--    order_id에 FK를 걸지 않는다: 조회 경로와 끊어 둔 보관소.
-- ---------------------------------------------------------------------------
CREATE TABLE pii_archive (
  id          INT      NOT NULL AUTO_INCREMENT,
  order_id    INT      NOT NULL,
  name_enc    TEXT     NOT NULL,
  phone_enc   TEXT     NOT NULL,
  address_enc TEXT     NOT NULL,
  archived_at DATETIME NOT NULL                      COMMENT 'KST, 옮겨진 시각',
  keep_until  DATETIME NOT NULL                      COMMENT 'KST, 이 시각이 지나면 파기 (archived_at + 5년)',
  PRIMARY KEY (id),
  KEY idx_pii_archive_order (order_id),
  KEY idx_pii_archive_keep_until (keep_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='보존용 개인정보. 보존 기간이 지나면 파기한다';

-- ---------------------------------------------------------------------------
-- 5. assignments : 날짜별 배정. 오늘 배정 = 로그인·조회 조건
-- ---------------------------------------------------------------------------
CREATE TABLE assignments (
  id        INT         NOT NULL AUTO_INCREMENT,
  driver_id VARCHAR(10) NOT NULL,
  order_id  INT         NOT NULL,
  work_date DATE        NOT NULL                     COMMENT 'KST 날짜',
  PRIMARY KEY (id),
  UNIQUE KEY uq_assignments_order_date (order_id, work_date),
  KEY idx_assignments_driver_date (driver_id, work_date),
  CONSTRAINT fk_assignments_driver FOREIGN KEY (driver_id) REFERENCES drivers (driver_id),
  CONSTRAINT fk_assignments_order  FOREIGN KEY (order_id)  REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='기사별 그날 배정 물량';

-- ---------------------------------------------------------------------------
-- 6. access_logs : 로그인·스캔·완료·파기 기록 (개인정보 없음)
--    driver_id에 FK를 걸지 않는다: 없는 ID로 로그인 시도한 것도 남긴다.
-- ---------------------------------------------------------------------------
CREATE TABLE access_logs (
  id        INT         NOT NULL AUTO_INCREMENT,
  driver_id VARCHAR(10) NOT NULL                     COMMENT '기사 ID 또는 ADMIN',
  order_id  INT         NULL,
  action    VARCHAR(20) NOT NULL                     COMMENT 'LOGIN / SCAN / COMPLETE / PURGE',
  result    VARCHAR(30) NOT NULL                     COMMENT 'OK 또는 오류 코드',
  `at`      DATETIME    NOT NULL                     COMMENT 'KST',
  PRIMARY KEY (id),
  KEY idx_access_logs_driver_at (driver_id, `at`),
  KEY idx_access_logs_at (`at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='접근 기록';

-- ---------------------------------------------------------------------------
-- 배송 완료 트리거
--   orders.status 가 DELIVERED 로 바뀌는 순간(이전 값이 DELIVERED 가 아닐 때)
--   recipient_pii → pii_archive 로 옮기고(keep_until = 지금 + 5년) recipient_pii 에서 지운다.
--   safe_number 는 보존하지 않는다.
--   시각은 서버 시간대와 상관없이 KST 가 되도록 UTC + 9시간으로 만든다.
-- ---------------------------------------------------------------------------
DELIMITER $$

CREATE TRIGGER trg_orders_delivered
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
  DECLARE v_now DATETIME;

  IF NEW.status = 'DELIVERED' AND OLD.status <> 'DELIVERED' THEN
    SET v_now = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 9 HOUR);

    INSERT INTO pii_archive (order_id, name_enc, phone_enc, address_enc, archived_at, keep_until)
    SELECT order_id, name_enc, phone_enc, address_enc, v_now, DATE_ADD(v_now, INTERVAL 5 YEAR)
    FROM recipient_pii
    WHERE order_id = NEW.id;

    DELETE FROM recipient_pii WHERE order_id = NEW.id;
  END IF;
END$$

DELIMITER ;
