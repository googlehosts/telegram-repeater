/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;

-- Dumping structure for table exam_user_session
CREATE TABLE IF NOT EXISTS `exam_user_session` (
  `user_id` int(11) NOT NULL,
  `problem_id` int(11) DEFAULT NULL,
  `timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `baned` tinyint(4) NOT NULL DEFAULT '0',
  `bypass` tinyint(4) NOT NULL DEFAULT '0' COMMENT 'bypass exam',
  `passed` tinyint(4) NOT NULL DEFAULT '0' COMMENT 'passed the exam',
  `unlimited` tinyint(4) NOT NULL DEFAULT '0',
  `retries` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Dumping structure for table msg_id
CREATE TABLE IF NOT EXISTS `msg_id` (
  `msg_id` int(11) NOT NULL,
  `target_id` int(11) NOT NULL DEFAULT '0',
  `timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `user_id` bigint(20) NOT NULL DEFAULT '0',
  PRIMARY KEY (`msg_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Dumping structure for table tickets
CREATE TABLE IF NOT EXISTS `tickets` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL DEFAULT '0',
  `timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `hash` varchar(32) DEFAULT '',
  `origin_msg` text,
  `section` varchar(20) DEFAULT '',
  `status` varchar(10) DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ticket` (`hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Dumping structure for table tickets_user
CREATE TABLE IF NOT EXISTS `tickets_user` (
  `user_id` bigint(20) NOT NULL,
  `create_time` timestamp NULL DEFAULT NULL,
  `last_time` timestamp NULL DEFAULT NULL,
  `baned` tinyint(4) NOT NULL DEFAULT '0',
  `last_msg_sent` timestamp NULL DEFAULT NULL,
  `step` tinyint(4) NOT NULL DEFAULT '0',
  `section` varchar(20) DEFAULT '',
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IF(@OLD_FOREIGN_KEY_CHECKS IS NULL, 1, @OLD_FOREIGN_KEY_CHECKS) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
