/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2024 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32f1xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
void Check_ADC_Connections(void);
/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */
typedef struct {
    GPIO_TypeDef* GPIO_Port;
    uint16_t GPIO_Pin;
} ChipSelectConfig;

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define LED_Pin GPIO_PIN_1
#define LED_GPIO_Port GPIOC
#define RESET_ADC_Pin GPIO_PIN_5
#define RESET_ADC_GPIO_Port GPIOC
#define BOOT1_Pin GPIO_PIN_2
#define BOOT1_GPIO_Port GPIOB
#define CS2_Pin GPIO_PIN_10
#define CS2_GPIO_Port GPIOB
#define CS1_Pin GPIO_PIN_11
#define CS1_GPIO_Port GPIOB
#define DRDY_Pin GPIO_PIN_12
#define DRDY_GPIO_Port GPIOB
#define DRDY_EXTI_IRQn EXTI15_10_IRQn
#define CS3_Pin GPIO_PIN_6
#define CS3_GPIO_Port GPIOC
#define CS4_Pin GPIO_PIN_7
#define CS4_GPIO_Port GPIOC
#define CLKOUT_Pin GPIO_PIN_8
#define CLKOUT_GPIO_Port GPIOA
#define KEY1_Pin GPIO_PIN_9
#define KEY1_GPIO_Port GPIOA
#define KEY2_Pin GPIO_PIN_10
#define KEY2_GPIO_Port GPIOA
#define BT_LED_Pin GPIO_PIN_4
#define BT_LED_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
