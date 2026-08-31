INSERT INTO market.strategic_sector_watchlist
(
    sector_type,sector_code,sector_name,strategic_theme,strategic_subtheme,
    watch_level,watch_reason,is_active,effective_from,effective_to,
    definition_source,definition_note
)
WITH seed AS
(
    SELECT * FROM VALUES(
        'sector_type String,sector_code String,strategic_theme String,strategic_subtheme String,watch_level String,watch_reason String',
        ('concept','886033.TI','AI基础设施','光互连','CORE','CPO是AI集群高速光互连的重要实现方向'),
        ('concept','885887.TI','AI基础设施','数据中心','CORE','数据中心承载AI训练和推理基础设施'),
        ('concept','886044.TI','AI基础设施','散热','CORE','液冷是高功率AI服务器的重要散热方向'),
        ('concept','886050.TI','AI基础设施','算力服务','CORE','算力租赁反映算力服务供给和商业化需求'),
        ('concept','885957.TI','AI基础设施','算力网络','CORE','东数西算连接数据中心布局和算力调度'),
        ('concept','886073.TI','AI基础设施','高速连接','CORE','高速铜连接是AI服务器短距高速互连方向'),
        ('concept','885959.TI','AI基础设施','PCB','CORE','高算力设备依赖高性能和高多层PCB'),
        ('industry','884092.TI','AI基础设施','PCB','CORE','印制电路板行业承载AI硬件板级互连'),
        ('style','883443.TI','AI基础设施','算力综合','CORE','目录中的算力主题精选用于观察算力综合表现'),
        ('concept','886048.TI','AI基础设施','GPU生态','CORE','英伟达产业链反映全球AI算力硬件景气'),

        ('concept','886058.TI','国产AI算力','国产算力芯片','CORE','华为昇腾是国产AI算力的重要实际映射'),
        ('concept','886009.TI','国产AI算力','先进封装','CORE','先进封装支撑高性能AI芯片集成'),
        ('concept','886042.TI','国产AI算力','存储','CORE','AI计算依赖高带宽和高容量存储体系'),
        ('industry','884287.TI','国产AI算力','芯片设计','CORE','数字芯片设计是国产算力芯片的核心环节'),
        ('concept','885843.TI','国产AI算力','芯片设计','CORE','华为海思是国产芯片设计的重要观察方向'),
        ('concept','885980.TI','国产AI算力','服务器处理器','CORE','华为鲲鹏映射国产服务器计算生态'),

        ('concept','886071.TI','AI终端','AI PC','CORE','AI PC是端侧模型和个人计算终端的重要载体'),
        ('concept','886070.TI','AI终端','AI手机','CORE','AI手机是端侧AI规模化落地的重要终端'),
        ('concept','886085.TI','AI终端','智能眼镜','CORE','AI眼镜结合多模态交互和端侧智能'),

        ('concept','886069.TI','具身智能','人形机器人','CORE','人形机器人是具身智能的直接整机映射'),
        ('concept','885517.TI','具身智能','机器人综合','CORE','机器人概念覆盖具身智能主要产业链'),
        ('concept','886008.TI','具身智能','核心执行器','CORE','减速器是机器人核心执行部件'),
        ('concept','886002.TI','具身智能','感知','CORE','机器视觉是机器人环境感知的重要环节'),
        ('industry','884218.TI','具身智能','机器人整机','CORE','机器人行业用于观察整机和产业链实际表现'),

        ('concept','886084.TI','AI基础设施','光通信','IMPORTANT','光纤是数据中心和算力网络连接基础'),
        ('industry','884262.TI','AI基础设施','通信网络设备','IMPORTANT','通信网络设备承载算力网络连接'),

        ('concept','885756.TI','国产AI算力','芯片综合','IMPORTANT','芯片概念较宽，作为国产算力上游综合观察'),
        ('industry','881121.TI','国产AI算力','半导体综合','IMPORTANT','半导体行业较宽，只作为算力上游综合观察'),
        ('industry','884229.TI','国产AI算力','半导体设备','IMPORTANT','半导体设备关系国产芯片制造自主化'),
        ('industry','884227.TI','国产AI算力','芯片制造','IMPORTANT','集成电路制造关系国产算力芯片供给'),
        ('industry','884228.TI','国产AI算力','封装测试','IMPORTANT','集成电路封测是芯片产业链关键环节'),

        ('concept','886019.TI','AI软件商业化','生成式AI','IMPORTANT','AIGC用于观察生成式AI应用扩散'),
        ('concept','886108.TI','AI软件商业化','AI应用','IMPORTANT','AI应用直接反映软件商业化映射'),
        ('concept','886099.TI','AI软件商业化','智能体','IMPORTANT','AI智能体是应用自动化的重要技术路线'),
        ('concept','886031.TI','AI软件商业化','大模型应用','IMPORTANT','ChatGPT概念用于观察大模型应用生态'),
        ('concept','885728.TI','AI软件商业化','人工智能综合','IMPORTANT','人工智能板块较宽，作为软件和应用综合观察'),
        ('concept','886062.TI','AI软件商业化','多模态','IMPORTANT','多模态AI连接文本、图像、语音和视频应用'),
        ('concept','885362.TI','AI软件商业化','云计算','IMPORTANT','云计算提供AI应用部署和推理基础'),
        ('concept','886094.TI','AI软件商业化','国产大模型','IMPORTANT','华为盘古是国产大模型的真实目录映射'),
        ('concept','886090.TI','AI软件商业化','国产大模型','IMPORTANT','智谱AI是国产大模型商业化观察方向'),

        ('concept','886046.TI','AI终端','MR','IMPORTANT','MR是空间计算和多模态交互终端'),
        ('concept','885454.TI','AI终端','可穿戴','IMPORTANT','智能穿戴是端侧AI的外围终端载体'),
        ('concept','885800.TI','AI终端','消费电子','IMPORTANT','消费电子范围较宽，用于观察AI终端产业扩散'),
        ('concept','885709.TI','AI终端','虚拟现实','IMPORTANT','虚拟现实用于观察沉浸式AI交互终端'),

        ('concept','885946.TI','具身智能','传感器','IMPORTANT','传感器是机器人感知系统基础'),
        ('industry','881171.TI','具身智能','自动化设备','IMPORTANT','自动化设备覆盖机器人外围执行和控制环节'),
        ('industry','881277.TI','具身智能','电机','IMPORTANT','电机是机器人动力和执行环节'),
        ('industry','884219.TI','具身智能','工业控制','IMPORTANT','工控设备支撑机器人控制和工业落地'),

        ('concept','885311.TI','AI能源基础设施','智能电网','IMPORTANT','智能电网关系AI数据中心新增电力的调度和接入'),
        ('concept','885425.TI','AI能源基础设施','输电','IMPORTANT','特高压关系跨区域电力输送能力'),
        ('concept','885921.TI','AI能源基础设施','储能','IMPORTANT','储能帮助平衡数据中心高负荷用电需求'),
        ('industry','881278.TI','AI能源基础设施','电网设备','IMPORTANT','电网设备承载新增电力基础设施建设'),
        ('industry','881282.TI','AI能源基础设施','电源设备','IMPORTANT','其他电源设备用于观察数据中心供电配套'),
        ('industry','881145.TI','AI能源基础设施','电力供给','IMPORTANT','电力行业反映AI基础设施的能源供给约束'),

        ('concept','886074.TI','AI软件商业化','AI语料','WATCH','AI语料值得跟踪但商业模式仍待持续验证'),
        ('concept','886068.TI','AI软件商业化','AI视频','WATCH','AI视频应用值得跟踪但盈利兑现差异较大'),
        ('concept','886102.TI','AI软件商业化','AI综合样本','WATCH','中国AI50作为目录中的AI综合观察样本')
    )
),
catalog AS
(
    SELECT sector_type,sector_code,argMax(sector_name,version_time) sector_name
    FROM market.sector_catalog
    WHERE is_active=1
    GROUP BY sector_type,sector_code
),
existing AS
(
    SELECT sector_type,sector_code,strategic_theme
    FROM market.strategic_sector_watchlist FINAL
)
SELECT
    s.sector_type,s.sector_code,c.sector_name,s.strategic_theme,s.strategic_subtheme,
    s.watch_level,s.watch_reason,1,toDate('2026-08-30'),CAST(NULL,'Nullable(Date)'),
    'AI_RESEARCH','AI战略主线V1；仅使用sector_catalog中的真实板块映射'
FROM seed s
INNER JOIN catalog c USING (sector_type,sector_code)
LEFT ANTI JOIN existing e USING (sector_type,sector_code,strategic_theme);
