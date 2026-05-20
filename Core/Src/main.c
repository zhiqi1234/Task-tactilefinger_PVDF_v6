/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "adc.h"
#include "dma.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "ads131m0x.h"
#include "stdio.h"
#include "string.h"
#include "queue.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
extern DMA_HandleTypeDef hdma_spi2_rx;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
// only 1 ADC chip
//uint8_t receiveData[24];
//uint8_t transmitData[transm_data_len];
//uint8_t receiveCommand[2];
//
//int cnt = 0;
//int test_flag = 0;
////int dataCpltFlag = 0;
//
//Queue *queue;

// all 4 ADC chip
uint8_t receiveData[4][27];  // 每个芯片27字节的数据缓冲区（3字节状态字 + 24字节通道数据）
uint8_t transmitData[4][transm_data_len]; // 每个芯片�???????????�???????????28字节的发送数据缓冲区+1个字节标签位
uint8_t transmitData_mix[1][transm_data_len]; // 每个芯片�???????????�???????????28字节的发送数据缓冲区+1个字节标签位
int currentChip = 0; // 当前处理的芯片索�???????????
int cnt = 0;
int test_flag = 0;
Queue *queues[5]; // 每个芯片�???????????个队�???????????

ChipSelectConfig csConfig[4] = {
    {CS1_GPIO_Port, CS1_Pin},
    {CS2_GPIO_Port, CS2_Pin},
    {CS3_GPIO_Port, CS3_Pin},
    {CS4_GPIO_Port, CS4_Pin}
};

uint8_t adc_connected[4] = {1,1,1,1};  // 存储每个ADC芯片的连接状�???????????

volatile uint8_t data_ready_flag = 0;  // DRDY中断标志位
volatile uint8_t uart_tx_busy = 0;     // UART DMA传输忙标志位
int tx_queue_index = 0;                // 当前发送的队列索引

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_UART4_Init();
  MX_USART2_UART_Init();
  MX_SPI2_Init();
  MX_ADC1_Init();
  MX_TIM2_Init();
  /* USER CODE BEGIN 2 */
  HAL_TIM_Base_Start_IT(&htim2); // 启动TIM2并使能中�???????????

  // only 1 ADC chip
//  queue = createQueue(10);

  // all 4 ADC chip
  for (int i = 0; i < 5; i++) {
	queues[i] = createQueue(50); // 为每个芯片创建队�??????????? 为混合adc芯片的数据存储单独创建队列
  }

//	HAL_GPIO_WritePin(CS1_GPIO_Port, CS1_Pin, GPIO_PIN_RESET);  // enable chip 1
	HAL_Delay(500);  // 等待ADC和模拟电源稳定
	adcStartup();
	HAL_Delay(100);
//	HAL_GPIO_WritePin(CS1_GPIO_Port, CS1_Pin, GPIO_PIN_SET);    // disable chip 1
//	HAL_GPIO_WritePin(CS1_GPIO_Port, CS1_Pin, GPIO_PIN_RESET);  // enable chip 1
	__HAL_GPIO_EXTI_CLEAR_IT(DRDY_Pin);  // 清除上电期间可能产生的伪中断标志位
	HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);


  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
	  // 轮询检查DRDY标志位
	  if (data_ready_flag == 1)
	  {
		  data_ready_flag = 0;  // 清除标志位

		  // 检查DRDY引脚状态
		  if (HAL_GPIO_ReadPin(DRDY_GPIO_Port, DRDY_Pin) == GPIO_PIN_RESET)
		  {
			  // 依次读取3个ADC芯片的数据
			  for (int i = 0; i < 3; i++)
			  {
				  // 拉低CS片选，使能当前ADC芯片
				  HAL_GPIO_WritePin(csConfig[i].GPIO_Port, csConfig[i].GPIO_Pin, GPIO_PIN_RESET);

				  // 读取27字节数据（3字节状态字 + 24字节通道数据）
				  HAL_SPI_Receive(&hspi2, receiveData[i], 27, 100);

				  // 检查数据有效性
				  if (receiveData[i][0] == 0xFF)
				  {
					  memset(receiveData[i], 0, 27);
				  }

				  // 拉高CS片选，禁用当前ADC芯片
				  HAL_GPIO_WritePin(csConfig[i].GPIO_Port, csConfig[i].GPIO_Pin, GPIO_PIN_SET);

				  // 组装发送数据帧
				  transmitData[i][0] = 0xAA;
				  transmitData[i][1] = 0xAA;
				  transmitData[i][2] = i;

				  // 跳过前3个字节状态字，复制24字节纯通道数据
				  memcpy(&transmitData[i][3], &receiveData[i][3], 24);

				  transmitData[i][27] = 0xFF;
				  transmitData[i][28] = 0xFF;

				  // 入队
				  enqueue(queues[i], transmitData[i]);

				  cnt++;
				  if(cnt % 1000 == 0)
					  HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
			  }
		  }
	  }

	  // 发送队列中的数据（每次只发一个，等DMA完成后再发下一个）
	  if (!uart_tx_busy)
	  {
		  for (int i = 0; i < 3; i++)
		  {
			  int idx = (tx_queue_index + i) % 3;
			  if (!isEmpty(queues[idx]))
			  {
				  QueueElement item = dequeue(queues[idx]);
				  uart_tx_busy = 1;
				  tx_queue_index = (idx + 1) % 3;
				  HAL_UART_Transmit_DMA(&huart4, item.data, transm_data_len);
				  break;
			  }
		  }
	  }

  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI|RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
  PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_ADC;
  PeriphClkInit.AdcClockSelection = RCC_ADCPCLK2_DIV6;
  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
  {
    Error_Handler();
  }
  HAL_RCC_MCOConfig(RCC_MCO, RCC_MCO1SOURCE_HSI, RCC_MCODIV_1);
}

/* USER CODE BEGIN 4 */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) {
    // �???????????查触发中断的是否是TIM2
    if (htim->Instance == TIM2)
    {
        // 调用DRDY引脚状�?�检查函�???????????
//        Check_ADC_Connections();
    	// 1 ADC chip
//    	if (!adc_connected[currentChip])
//    	{
//    		transmitData[currentChip][0] = 0xAA;
//			transmitData[currentChip][1] = 0xAA;
//			int idx = 2;
//			for (int i = 0; i < 24 && idx < sizeof(transmitData[currentChip]); i += 3) {
//				transmitData[currentChip][idx] = receiveData[currentChip][i];
//				transmitData[currentChip][idx + 1] = receiveData[currentChip][i + 1];
//				transmitData[currentChip][idx + 2] = receiveData[currentChip][i + 2];
//				idx += 3;
//			}
//			transmitData[currentChip][26] = 0xFF;
//			transmitData[currentChip][27] = 0xFF;
//
//			cnt++;
//			if(cnt % 100 == 0)
//				HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
//			enqueue(queues[currentChip], transmitData[currentChip]);
//
//			Check_ADC_Connections();
//    	}

    	// 4 ADC chips
//    	for (int i = 0; i < 4; i++){
//			if (!adc_connected[i])
//			{
//				transmitData[i][0] = 0xAA;
//				transmitData[i][1] = 0xAA;
//				int idx = 2;
//				for (int j = 0; j < 24 && idx < sizeof(transmitData[i]); j += 3) {
//					transmitData[i][idx] = receiveData[i][j];
//					transmitData[i][idx + 1] = receiveData[i][j + 1];
//					transmitData[i][idx + 2] = receiveData[i][j + 2];
//					idx += 3;
//				}
//				transmitData[i][26] = 0xFF;
//				transmitData[i][27] = 0xFF;
//
//				cnt++;
//				if(cnt % 100 == 0)
//					HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
//				enqueue(queues[i], transmitData[i]);
//
//				Check_ADC_Connections();
//			}
//    	}
    }
}


void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
	if(GPIO_Pin == DRDY_Pin){
		// 仅设置标志位，不执行任何阻塞操作
		data_ready_flag = 1;
	}
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
	if (huart == &huart4)
	{
		uart_tx_busy = 0;  // DMA传输完成，清除忙标志
	}
}


void HAL_SPI_RxCpltCallback(SPI_HandleTypeDef *hspi)
{

	if(hspi == &hspi2){
//		// only 1 ADC chip
//		HAL_GPIO_WritePin(CS1_GPIO_Port, CS1_Pin, GPIO_PIN_SET);  // disable chip 1
//
//		// gpt优化数据读取保持结果
//		transmitData[0] = 0xAA;
//		transmitData[1] = 0xAA;
//		int idx = 2;  // 用于 transmitData 的索�???????????
//		// 遍历接收到的数据，将 24 位数据按原样存储�??????????? transmitData �???????????
//		for (int i = 0; i < 24 && idx < sizeof(transmitData); i += 3)
//		{
//			// �??????????? receiveData 中的 3 字节数据直接存储�??????????? transmitData �???????????
//			transmitData[idx] = receiveData[i];
//			transmitData[idx + 1] = receiveData[i + 1];
//			transmitData[idx + 2] = receiveData[i + 2];
//
//			idx += 3;  // 每次处理 3 个字节数据，�???????????以索引加 3
//		}
//		transmitData[26] = 0xFF;
//		transmitData[27] = 0xFF;
//
//		cnt++;
//		if(cnt % 1000 == 0)
//			HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
//		enqueue(queue, transmitData);

		// all 4 ADC chip,send 1 ADC data
//		// 禁用当前的ADC芯片片�?�信�???????????
//		HAL_GPIO_WritePin(csConfig[currentChip].GPIO_Port, csConfig[currentChip].GPIO_Pin, GPIO_PIN_SET);
//
//		// 处理当前ADC芯片的数�???????????
//		transmitData[currentChip][0] = 0xAA;
//		transmitData[currentChip][1] = 0xAA;
//		int idx = 2;
//		for (int i = 0; i < 24 && idx < sizeof(transmitData[currentChip]); i += 3) {
//			transmitData[currentChip][idx] = receiveData[currentChip][i];
//			transmitData[currentChip][idx + 1] = receiveData[currentChip][i + 1];
//			transmitData[currentChip][idx + 2] = receiveData[currentChip][i + 2];
//			idx += 3;
//		}
//		transmitData[currentChip][26] = 0xFF;
//		transmitData[currentChip][27] = 0xFF;
//
//		cnt++;
//		if(cnt % 1000 == 0)
//			HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
//		enqueue(queues[currentChip], transmitData[currentChip]);

		// 切换到下�???????????个ADC芯片
//		currentChip = (currentChip + 1) % 4;

		// all 4 ADC chip,send 4 ADC data
//		for (int i = 0; i < 4; i++){
//			// 禁用当前的ADC芯片片�?�信�???????????
////			HAL_GPIO_WritePin(csConfig[i].GPIO_Port, csConfig[i].GPIO_Pin, GPIO_PIN_SET);
//
//			// 处理当前ADC芯片的数�???????????
//			transmitData[i][0] = 0xAA;
//			transmitData[i][1] = 0xAA;
//			int idx = 2;
//			for (int j = 0; j < 24 && idx < sizeof(transmitData[i]); j += 3) {
//				transmitData[i][idx] = receiveData[i][j];
//				transmitData[i][idx + 1] = receiveData[i][j + 1];
//				transmitData[i][idx + 2] = receiveData[i][j + 2];
//				idx += 3;
//			}
//			transmitData[i][26] = 0xFF;
//			transmitData[i][27] = 0xFF;
//
//			cnt++;
//			if(cnt % 1000 == 0)
//				HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
//			enqueue(queues[i], transmitData[i]);
//		}
//		// 禁用当前的ADC芯片片�?�信�???????????
//		HAL_GPIO_WritePin(csConfig[currentChip].GPIO_Port, csConfig[currentChip].GPIO_Pin, GPIO_PIN_SET);

	}
}

void Check_ADC_Connections(void)
{
    for (int i = 0; i < 4; i++) {
        HAL_GPIO_WritePin(csConfig[i].GPIO_Port, csConfig[i].GPIO_Pin, GPIO_PIN_RESET);
        HAL_Delay(1);  // 短暂延时，等待ADC芯片响应

        if (HAL_GPIO_ReadPin(DRDY_GPIO_Port, DRDY_Pin) == GPIO_PIN_RESET) {
            // 如果DRDY引脚被拉低，说明ADC已连接并准备�???????????
            adc_connected[i] = 1;
        } else {
            // 否则，可能未连接或未正常工作
            adc_connected[i] = 0;
        }

        HAL_GPIO_WritePin(csConfig[i].GPIO_Port, csConfig[i].GPIO_Pin, GPIO_PIN_SET);
    }

    // 可以根据adc_connected数组中的结果来做进一步处�???????????
    // 例如记录日志，或者�?�知主程序处理连接错�???????????
}


/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
