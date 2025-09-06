-- SQL script to create database and user for Synclet
-- Run this as MySQL root user

-- Create database if not exists
CREATE DATABASE IF NOT EXISTS synclet_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create user if not exists (MySQL 5.7+ syntax)
CREATE USER IF NOT EXISTS 'synclet_test'@'localhost' IDENTIFIED BY 'Bingo-Shrubbery-Crushing-428';

-- Grant all privileges on the database
GRANT ALL PRIVILEGES ON synclet_test.* TO 'synclet_test'@'localhost';

-- Apply the changes
FLUSH PRIVILEGES;

-- Display confirmation
SELECT 'Database and user created successfully!' AS Status;
